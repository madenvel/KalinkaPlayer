"""Data model and exception types for the procedural artwork generator.

The dataclasses here are intentionally plain and frozen: ``AlbumArtworkInput``
is what callers hand in, ``ArtworkParameters`` is the fully resolved,
deterministic recipe that :meth:`ProceduralArtworkGenerator.render` turns into
pixels.  Everything a renderer needs must live in ``ArtworkParameters`` so
that identical parameters always produce identical images.
"""

from dataclasses import dataclass

import numpy as np


class ArtworkError(Exception):
    """Base class for all procedural artwork errors."""


class InvalidInputError(ArtworkError, TypeError):
    """Album metadata is missing or of the wrong type."""


class InvalidSizeError(ArtworkError, ValueError):
    """Requested output size is not a valid square pixel size."""


class InvalidEmbeddingError(ArtworkError, ValueError):
    """Supplied album embedding is not a usable 1-D finite numeric array."""


class UnsupportedFormatError(ArtworkError, ValueError):
    """Requested image format (or file extension) is not supported."""


class RenderError(ArtworkError, RuntimeError):
    """A template renderer failed to produce an image."""


class FileWriteError(ArtworkError, OSError):
    """Writing the generated artwork to disk failed."""


@dataclass(frozen=True)
class AlbumArtworkInput:
    """Album metadata used to derive deterministic artwork.

    Only ``artist`` and ``title`` are required.  ``release_group_id`` (a
    MusicBrainz release-group MBID) is the strongest family signal; without it
    a fallback family identity is derived from normalized artist + base title
    + track-list signature.  ``album_embedding`` optionally steers the visual
    style (never family membership).
    """

    artist: str
    title: str
    genre: str | None = None
    release_group_id: str | None = None
    release_id: str | None = None
    track_titles: tuple[str, ...] = ()
    album_embedding: np.ndarray | None = None
    embedding_version: str | None = None


@dataclass(frozen=True)
class ArtworkParameters:
    """Fully resolved, deterministic rendering recipe for one artwork.

    ``render()`` depends only on this object: the seeds drive all geometry
    RNG streams, the scalar knobs are already family/edition interpolated,
    and the palette is final RGB.  All scalar style values live in ``[0, 1]``.
    """

    generator_version: int
    family_key: str
    family_seed: int
    edition_seed: int
    template: str
    palette: tuple[tuple[int, int, int], ...]
    complexity: float
    softness: float
    contrast: float
    grain: float
    edition_strength: float
    resolved_size: int
    # Additional resolved style values (all in [0, 1], size-independent).
    hue: float
    saturation: float
    lightness: float
    angularity: float
    texture_density: float
    edition_kind: str
