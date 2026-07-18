"""Deterministic procedural album artwork generator.

High-level flow: metadata normalization -> family/edition identity -> seeds
-> semantic/genre style -> parameter resolution -> template rendering ->
post-processing -> encoding.  Parameter resolution and rendering are
synchronous and pure; the public async methods offload the CPU-bound work
with ``asyncio.to_thread`` so the event loop stays responsive.

The class holds no hidden mutable state and keeps no reference to rendered
images, so a single instance can be reused for concurrent calls.  It does
not limit concurrency itself; on a Raspberry Pi 4 the caller should wrap
calls in an ``asyncio.Semaphore(1)`` or ``(2)``.
"""

import asyncio
import io
import os
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from .identity import build_identity, rng_for
from .models import (
    AlbumArtworkInput,
    ArtworkError,
    ArtworkParameters,
    FileWriteError,
    InvalidInputError,
    InvalidSizeError,
    RenderError,
    UnsupportedFormatError,
)
from .styles import (
    DOMAIN_EDITION_STYLE,
    DOMAIN_STYLE,
    STYLE_FIELDS,
    build_palette,
    choose_template,
    embedding_units,
    match_genre_profile,
    resolve_style_values,
)
from .templates import DOMAIN_GRAIN, RENDERERS

GENERATOR_VERSION = 1

MIN_SIZE = 32
MAX_SIZE = 2048
DEFAULT_SIZE = 512

# Supersampling for antialiasing: 2x up to this output size, 1x beyond, so
# the largest transient canvas stays Pi-friendly (~2560 px square).
_SUPERSAMPLE_LIMIT = 1280

_FORMAT_EXTENSIONS = {
    "PNG": (".png",),
    "JPEG": (".jpg", ".jpeg"),
    "WEBP": (".webp",),
}
_EXTENSION_TO_FORMAT = {
    ext: fmt for fmt, exts in _FORMAT_EXTENSIONS.items() for ext in exts
}


class ProceduralArtworkGenerator:
    """Deterministic album artwork generator (Pillow + NumPy only).

    Reusable and safe for concurrent async calls; recommended concurrency on
    a Raspberry Pi 4 is 1-2 simultaneous renders (enforced by the caller).
    """

    def __init__(
        self, *, default_size: int = DEFAULT_SIZE, generator_version: int = GENERATOR_VERSION
    ) -> None:
        self._default_size = self._validate_size(default_size)
        if isinstance(generator_version, bool) or not isinstance(generator_version, int):
            raise InvalidInputError(
                f"generator_version must be int, got {type(generator_version).__name__}"
            )
        if generator_version < 1:
            raise InvalidInputError(
                f"generator_version must be >= 1, got {generator_version}"
            )
        self._generator_version = generator_version

    @property
    def generator_version(self) -> int:
        """Version stamped into every seed; bumping it reflows all artwork."""
        return self._generator_version

    @property
    def default_size(self) -> int:
        """Output size used when a call does not specify one."""
        return self._default_size

    # ------------------------------------------------------------------
    # Synchronous core
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_size(size) -> int:
        if isinstance(size, bool) or not isinstance(size, int):
            raise InvalidSizeError(
                f"size must be an int, got {type(size).__name__}: {size!r}"
            )
        if not MIN_SIZE <= size <= MAX_SIZE:
            raise InvalidSizeError(
                f"size must be within [{MIN_SIZE}, {MAX_SIZE}], got {size}"
            )
        return size

    def resolve_parameters(
        self, album: AlbumArtworkInput, *, size: int | None = None
    ) -> ArtworkParameters:
        """Resolve album metadata into a deterministic rendering recipe.

        The output size influences only ``resolved_size``; family and edition
        seeds and all style values are size-independent.
        """
        resolved_size = self._default_size if size is None else self._validate_size(size)
        identity = build_identity(album, self._generator_version)
        profile = match_genre_profile(album.genre)

        if album.album_embedding is not None:
            family_units = embedding_units(
                album.album_embedding, album.embedding_version, len(STYLE_FIELDS)
            )
        else:
            family_units = rng_for(identity.family_seed, DOMAIN_STYLE).random(
                len(STYLE_FIELDS)
            )
        family_style = resolve_style_values(profile, family_units)

        edition_units = rng_for(identity.edition_seed, DOMAIN_EDITION_STYLE).random(
            len(STYLE_FIELDS)
        )
        edition_style = resolve_style_values(profile, edition_units)

        # Bounded interpolation keeps the edition subordinate to the family:
        # both values sit inside the same profile range, so the offset can
        # never exceed edition_strength * range width.
        strength = identity.edition_strength
        style = {
            field: family_style[field] * (1.0 - strength) + edition_style[field] * strength
            for field in STYLE_FIELDS
        }

        template = choose_template(profile, identity.family_seed)
        palette = build_palette(
            style["hue"], style["saturation"], style["lightness"], identity.family_seed
        )
        return ArtworkParameters(
            generator_version=self._generator_version,
            family_key=identity.family_key,
            family_seed=identity.family_seed,
            edition_seed=identity.edition_seed,
            template=template,
            palette=palette,
            complexity=style["complexity"],
            softness=style["softness"],
            contrast=style["contrast"],
            grain=style["grain"],
            edition_strength=strength,
            resolved_size=resolved_size,
            hue=style["hue"] % 1.0,
            saturation=style["saturation"],
            lightness=style["lightness"],
            angularity=style["angularity"],
            texture_density=style["texture_density"],
            edition_kind=identity.edition_kind,
        )

    def render(self, parameters: ArtworkParameters) -> Image.Image:
        """Render an RGB image from resolved parameters (pure, synchronous).

        Identical parameters always produce identical pixel data within the
        same Pillow/NumPy environment.
        """
        if not isinstance(parameters, ArtworkParameters):
            raise InvalidInputError(
                f"parameters must be ArtworkParameters, got {type(parameters).__name__}"
            )
        renderer = RENDERERS.get(parameters.template)
        if renderer is None:
            raise RenderError(
                f"unknown template {parameters.template!r}; "
                f"expected one of {sorted(RENDERERS)}"
            )
        size = self._validate_size(parameters.resolved_size)
        if (
            not isinstance(parameters.palette, tuple)
            or len(parameters.palette) < 5
        ):
            raise InvalidInputError("palette must be a tuple of at least 5 RGB colors")

        supersample = 2 if size <= _SUPERSAMPLE_LIMIT else 1
        canvas = size * supersample
        try:
            image = renderer(parameters, canvas)
            if supersample > 1:
                image = image.resize((size, size), Image.Resampling.LANCZOS)
            return self._postprocess(image, parameters)
        except ArtworkError:
            raise
        except Exception as exc:
            raise RenderError(
                f"template {parameters.template!r} failed for family "
                f"{parameters.family_key!r} at size {size}: {exc}"
            ) from exc

    def _postprocess(self, image: Image.Image, parameters: ArtworkParameters) -> Image.Image:
        """Apply contrast shaping and edition-seeded film grain."""
        size = parameters.resolved_size
        if parameters.softness > 0.6 and size >= 96:
            # Extra global softening for very soft styles; skipped at tiny
            # sizes where it would wash out the composition.
            image = image.filter(
                ImageFilter.GaussianBlur(radius=(parameters.softness - 0.6) * size * 0.004)
            )
        array = np.asarray(image, dtype=np.float32)
        contrast_gain = 0.82 + parameters.contrast * 0.42
        array = (array - 127.5) * contrast_gain + 127.5
        if parameters.grain > 0.0:
            grain_rng = rng_for(parameters.edition_seed, DOMAIN_GRAIN)
            noise = grain_rng.standard_normal((size, size, 1), dtype=np.float32)
            # Scale grain down at tiny sizes so single pixels stay readable.
            amplitude = parameters.grain * 12.0 * min(1.0, size / 192.0)
            array += noise * amplitude
        np.clip(array, 0.0, 255.0, out=array)
        return Image.fromarray(array.astype(np.uint8), "RGB")

    # ------------------------------------------------------------------
    # Encoding / file output helpers (synchronous, run in worker threads)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_format(image_format) -> str:
        if not isinstance(image_format, str):
            raise UnsupportedFormatError(
                f"image_format must be str, got {type(image_format).__name__}"
            )
        normalized = image_format.strip().upper()
        if normalized == "JPG":
            normalized = "JPEG"
        if normalized not in _FORMAT_EXTENSIONS:
            raise UnsupportedFormatError(
                f"unsupported image format {image_format!r}; "
                f"supported: {sorted(_FORMAT_EXTENSIONS)}"
            )
        return normalized

    @staticmethod
    def _format_for_path(path: Path) -> str:
        suffix = path.suffix.lower()
        fmt = _EXTENSION_TO_FORMAT.get(suffix)
        if fmt is None:
            raise UnsupportedFormatError(
                f"cannot infer image format from extension {suffix!r} of "
                f"{path.name!r}; supported extensions: {sorted(_EXTENSION_TO_FORMAT)}"
            )
        return fmt

    def _render_and_encode(self, parameters: ArtworkParameters, image_format: str) -> bytes:
        image = self.render(parameters)
        buffer = io.BytesIO()
        image.save(buffer, format=image_format)
        return buffer.getvalue()

    def _render_to_file(
        self, parameters: ArtworkParameters, path: Path, image_format: str
    ) -> None:
        image = self.render(parameters)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FileWriteError(
                f"cannot create parent directory {str(path.parent)!r}: {exc}"
            ) from exc
        # Atomic write: encode into a sibling temp file, replace on success.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".artwork-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                image.save(handle, format=image_format)
            os.replace(tmp_name, path)
        except Exception as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            if isinstance(exc, ArtworkError):
                raise
            raise FileWriteError(
                f"failed to write artwork to {str(path)!r}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------

    async def generate(
        self, album: AlbumArtworkInput, *, size: int | None = None
    ) -> Image.Image:
        """Resolve parameters and render off the event-loop thread."""
        parameters = self.resolve_parameters(album, size=size)
        return await asyncio.to_thread(self.render, parameters)

    async def generate_bytes(
        self,
        album: AlbumArtworkInput,
        *,
        size: int | None = None,
        image_format: str = "PNG",
    ) -> bytes:
        """Render and encode to bytes without touching disk."""
        fmt = self._normalize_format(image_format)
        parameters = self.resolve_parameters(album, size=size)
        return await asyncio.to_thread(self._render_and_encode, parameters, fmt)

    async def generate_to_file(
        self,
        album: AlbumArtworkInput,
        output_path: Path,
        *,
        size: int | None = None,
        image_format: str | None = None,
    ) -> ArtworkParameters:
        """Render and atomically write to ``output_path``.

        The format is inferred from the file extension unless supplied.
        Returns the resolved :class:`ArtworkParameters` for the written file.
        """
        path = Path(output_path)
        if image_format is None:
            fmt = self._format_for_path(path)
        else:
            fmt = self._normalize_format(image_format)
        parameters = self.resolve_parameters(album, size=size)
        await asyncio.to_thread(self._render_to_file, parameters, path, fmt)
        return parameters
