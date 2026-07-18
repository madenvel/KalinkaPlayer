"""Genre profiles, semantic style mapping and palette construction.

Genre guides the visual style (allowed templates and ranges for hue,
saturation, complexity, ...) without homogenizing it: the concrete position
inside each range comes either from the album embedding (semantic style) or
from the family seed.  Edition identity then nudges the values by a bounded
interpolation, never regenerating the composition.
"""

import colorsys
import math
import re
from dataclasses import dataclass

import numpy as np

from .identity import rng_for, semantic_hash
from .models import InvalidEmbeddingError

# RNG stream domains (paired with family/edition seeds via rng_for()).
DOMAIN_STYLE = 12
DOMAIN_EDITION_STYLE = 13
DOMAIN_TEMPLATE = 11
DOMAIN_PALETTE = 14

# Order of the scalar style values; each is resolved into [lo, hi] from the
# matching GenreProfile range.  All ranges live inside [0, 1] except hue,
# which may wrap past 1.0 (taken mod 1 when building colors).
STYLE_FIELDS = (
    "hue",
    "saturation",
    "lightness",
    "softness",
    "complexity",
    "contrast",
    "grain",
    "angularity",
    "texture_density",
)


@dataclass(frozen=True)
class GenreProfile:
    """Value ranges and template preferences for one genre."""

    name: str
    templates: tuple[str, ...]
    hue: tuple[float, float]
    saturation: tuple[float, float]
    lightness: tuple[float, float]
    softness: tuple[float, float]
    complexity: tuple[float, float]
    contrast: tuple[float, float]
    grain: tuple[float, float]
    angularity: tuple[float, float]
    texture_density: tuple[float, float]


GENRE_PROFILES: dict[str, GenreProfile] = {
    "ambient": GenreProfile(
        name="ambient",
        templates=("waves", "horizon"),
        hue=(0.45, 0.70),
        saturation=(0.25, 0.55),
        lightness=(0.45, 0.70),
        softness=(0.65, 0.95),
        complexity=(0.15, 0.40),
        contrast=(0.10, 0.35),
        grain=(0.05, 0.25),
        angularity=(0.00, 0.20),
        texture_density=(0.20, 0.50),
    ),
    "electronic": GenreProfile(
        name="electronic",
        templates=("orbits", "blocks", "waves"),
        hue=(0.50, 0.95),
        saturation=(0.55, 0.85),
        lightness=(0.40, 0.60),
        softness=(0.30, 0.60),
        complexity=(0.45, 0.75),
        contrast=(0.45, 0.75),
        grain=(0.10, 0.30),
        angularity=(0.40, 0.70),
        texture_density=(0.40, 0.70),
    ),
    "techno": GenreProfile(
        name="techno",
        templates=("blocks", "slashes"),
        hue=(0.55, 1.00),
        saturation=(0.50, 0.90),
        lightness=(0.25, 0.45),
        softness=(0.10, 0.35),
        complexity=(0.55, 0.85),
        contrast=(0.65, 0.90),
        grain=(0.15, 0.35),
        angularity=(0.70, 0.95),
        texture_density=(0.50, 0.80),
    ),
    "jazz": GenreProfile(
        name="jazz",
        templates=("orbits", "slashes", "waves"),
        hue=(0.02, 0.16),
        saturation=(0.45, 0.75),
        lightness=(0.35, 0.60),
        softness=(0.45, 0.75),
        complexity=(0.35, 0.65),
        contrast=(0.40, 0.65),
        grain=(0.20, 0.45),
        angularity=(0.30, 0.60),
        texture_density=(0.30, 0.60),
    ),
    "classical": GenreProfile(
        name="classical",
        templates=("horizon", "waves", "orbits"),
        hue=(0.05, 0.60),
        saturation=(0.10, 0.35),
        lightness=(0.55, 0.80),
        softness=(0.55, 0.85),
        complexity=(0.20, 0.45),
        contrast=(0.25, 0.50),
        grain=(0.05, 0.20),
        angularity=(0.10, 0.35),
        texture_density=(0.20, 0.40),
    ),
    "rock": GenreProfile(
        name="rock",
        templates=("slashes", "blocks", "waves"),
        hue=(0.90, 1.15),
        saturation=(0.50, 0.80),
        lightness=(0.30, 0.55),
        softness=(0.20, 0.50),
        complexity=(0.45, 0.75),
        contrast=(0.55, 0.80),
        grain=(0.30, 0.55),
        angularity=(0.55, 0.85),
        texture_density=(0.40, 0.70),
    ),
    "hip hop": GenreProfile(
        name="hip hop",
        templates=("blocks", "slashes", "orbits"),
        hue=(0.05, 0.95),
        saturation=(0.60, 0.90),
        lightness=(0.30, 0.50),
        softness=(0.20, 0.45),
        complexity=(0.40, 0.70),
        contrast=(0.60, 0.85),
        grain=(0.15, 0.40),
        angularity=(0.60, 0.85),
        texture_density=(0.40, 0.65),
    ),
    "metal": GenreProfile(
        name="metal",
        templates=("slashes", "blocks"),
        hue=(0.55, 0.80),
        saturation=(0.15, 0.45),
        lightness=(0.15, 0.35),
        softness=(0.05, 0.30),
        complexity=(0.55, 0.85),
        contrast=(0.70, 0.95),
        grain=(0.35, 0.60),
        angularity=(0.75, 1.00),
        texture_density=(0.50, 0.80),
    ),
    "folk": GenreProfile(
        name="folk",
        templates=("horizon", "waves"),
        hue=(0.06, 0.20),
        saturation=(0.30, 0.60),
        lightness=(0.45, 0.70),
        softness=(0.50, 0.80),
        complexity=(0.25, 0.50),
        contrast=(0.30, 0.55),
        grain=(0.25, 0.50),
        angularity=(0.10, 0.40),
        texture_density=(0.30, 0.55),
    ),
    "default": GenreProfile(
        name="default",
        templates=("waves", "orbits", "horizon", "blocks", "slashes"),
        hue=(0.00, 1.00),
        saturation=(0.35, 0.75),
        lightness=(0.35, 0.65),
        softness=(0.30, 0.70),
        complexity=(0.30, 0.70),
        contrast=(0.35, 0.70),
        grain=(0.10, 0.40),
        angularity=(0.30, 0.70),
        texture_density=(0.30, 0.60),
    ),
}

# Alias phrase -> profile name; matched with word boundaries against the
# normalized genre string, so multi-value strings like "ambient electronic"
# or "jazz fusion" resolve sensibly.
GENRE_ALIASES: dict[str, str] = {
    "hiphop": "hip hop",
    "hip-hop": "hip hop",
    "rap": "hip hop",
    "trap": "hip hop",
    "idm": "electronic",
    "edm": "electronic",
    "electronica": "electronic",
    "synthwave": "electronic",
    "downtempo": "electronic",
    "house": "techno",
    "trance": "techno",
    "minimal": "techno",
    "drone": "ambient",
    "chillout": "ambient",
    "new age": "ambient",
    "orchestral": "classical",
    "symphony": "classical",
    "baroque": "classical",
    "opera": "classical",
    "chamber": "classical",
    "punk": "rock",
    "grunge": "rock",
    "alternative": "rock",
    "indie": "rock",
    "hard rock": "rock",
    "black metal": "metal",
    "death metal": "metal",
    "doom": "metal",
    "metalcore": "metal",
    "acoustic": "folk",
    "country": "folk",
    "americana": "folk",
    "singer songwriter": "folk",
    "bebop": "jazz",
    "swing": "jazz",
    "fusion": "jazz",
    "blues": "jazz",
    "soul": "jazz",
}


def _normalize_genre(genre: str) -> str:
    return re.sub(r"[\W_]+", " ", genre.casefold()).strip()


def match_genre_profile(genre: str | None) -> GenreProfile:
    """Resolve a (possibly multi-value) genre string to a profile.

    Matches profile names and aliases with word boundaries, in order of
    appearance; when two distinct profiles match ("ambient electronic") their
    numeric ranges are blended and template preferences concatenated.
    Unknown or missing genres fall back to the default profile.
    """
    if not genre:
        return GENRE_PROFILES["default"]
    normalized = _normalize_genre(genre)
    if not normalized:
        return GENRE_PROFILES["default"]

    phrases = [(name, name) for name in GENRE_PROFILES if name != "default"]
    phrases += [(alias, target) for alias, target in GENRE_ALIASES.items()]
    # Longest phrases first so "hard rock" beats "rock".
    phrases.sort(key=lambda item: len(item[0]), reverse=True)

    matches: list[tuple[int, str]] = []
    claimed = normalized
    for phrase, target in phrases:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        found = re.search(pattern, claimed)
        if found:
            matches.append((found.start(), target))
            # Blank out the matched span so shorter phrases can't re-match it.
            claimed = claimed[: found.start()] + " " * len(found.group(0)) + claimed[found.end():]

    seen: list[str] = []
    for _, target in sorted(matches):
        if target not in seen:
            seen.append(target)
    if not seen:
        return GENRE_PROFILES["default"]
    if len(seen) == 1:
        return GENRE_PROFILES[seen[0]]
    return blend_profiles([GENRE_PROFILES[name] for name in seen[:2]])


def blend_profiles(profiles: list[GenreProfile]) -> GenreProfile:
    """Blend two or more profiles: mean numeric ranges, union of templates."""
    templates: list[str] = []
    for profile in profiles:
        for template in profile.templates:
            if template not in templates:
                templates.append(template)
    blended = {}
    for field in STYLE_FIELDS:
        los, his = zip(*(getattr(p, field) for p in profiles))
        blended[field] = (sum(los) / len(los), sum(his) / len(his))
    return GenreProfile(
        name="+".join(p.name for p in profiles),
        templates=tuple(templates),
        **blended,
    )


def validate_embedding(embedding: np.ndarray) -> np.ndarray:
    """Validate an album embedding and return a float64 copy.

    Requires a non-empty 1-D numeric ndarray with finite values.  The input
    array is never mutated.
    """
    if not isinstance(embedding, np.ndarray):
        raise InvalidEmbeddingError(
            f"album_embedding must be a numpy ndarray, got {type(embedding).__name__}"
        )
    if embedding.ndim != 1:
        raise InvalidEmbeddingError(
            f"album_embedding must be 1-D, got shape {embedding.shape}"
        )
    if embedding.size == 0:
        raise InvalidEmbeddingError("album_embedding must be non-empty")
    if not (
        np.issubdtype(embedding.dtype, np.floating)
        or np.issubdtype(embedding.dtype, np.integer)
    ):
        raise InvalidEmbeddingError(
            f"album_embedding must be numeric, got dtype {embedding.dtype}"
        )
    values = np.array(embedding, dtype=np.float64, copy=True)
    if not np.all(np.isfinite(values)):
        raise InvalidEmbeddingError("album_embedding contains NaN or infinite values")
    return values


def embedding_units(
    embedding: np.ndarray, embedding_version: str | None, count: int
) -> np.ndarray:
    """Project an embedding onto ``count`` deterministic unit values in (0, 1).

    Uses fixed, constant-seeded pseudo-random projection vectors (salted by
    the embedding version), so the mapping is stable across processes and
    similar embeddings land on nearby values.
    """
    values = validate_embedding(embedding)
    norm = float(np.linalg.norm(values))
    if norm > 0.0:
        values = values / norm
    base_seed = semantic_hash(("semantic-projection", embedding_version or ""))
    units = np.empty(count, dtype=np.float64)
    for i in range(count):
        projection = rng_for(base_seed, i).standard_normal(values.size)
        # ||values|| == 1 so the dot product is ~N(0, 1); tanh squashes it
        # smoothly into (-1, 1) before shifting to (0, 1).
        units[i] = 0.5 * (1.0 + math.tanh(1.2 * float(values @ projection)))
    return units


def resolve_style_values(profile: GenreProfile, units: np.ndarray) -> dict[str, float]:
    """Position each style scalar inside its profile range using unit values."""
    style = {}
    for field, unit in zip(STYLE_FIELDS, units):
        lo, hi = getattr(profile, field)
        style[field] = lo + (hi - lo) * float(unit)
    return style


def choose_template(profile: GenreProfile, family_seed: int) -> str:
    """Family-seeded template choice among the profile's allowed templates."""
    rng = rng_for(family_seed, DOMAIN_TEMPLATE)
    return profile.templates[int(rng.integers(len(profile.templates)))]


def _clamp(value: float, lo: float = 0.03, hi: float = 0.97) -> float:
    return max(lo, min(hi, value))


def _hsl_rgb(hue: float, saturation: float, lightness: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(hue % 1.0, _clamp(lightness), _clamp(saturation))
    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def build_palette(
    hue: float, saturation: float, lightness: float, family_seed: int
) -> tuple[tuple[int, int, int], ...]:
    """Build the 5-color palette: background, three principals, one accent.

    The structural choices (analogous offset, accent placement) come from the
    family seed only, so all editions of an album share the same palette
    structure; the small edition hue/saturation shifts arrive through the
    already-interpolated scalar inputs.
    """
    rng = rng_for(family_seed, DOMAIN_PALETTE)
    analog = 0.03 + 0.06 * float(rng.random())
    accent_offset = 0.38 + 0.24 * float(rng.random())
    background = _hsl_rgb(hue, saturation * 0.55, _clamp(lightness - 0.28, 0.05, 0.92))
    primary = _hsl_rgb(hue, saturation, lightness)
    secondary = _hsl_rgb(hue + analog, saturation * 0.9, _clamp(lightness + 0.12))
    tertiary = _hsl_rgb(hue - analog, saturation * 0.85, _clamp(lightness - 0.10))
    accent = _hsl_rgb(hue + accent_offset, _clamp(saturation * 1.15), _clamp(lightness + 0.18))
    return (background, primary, secondary, tertiary, accent)
