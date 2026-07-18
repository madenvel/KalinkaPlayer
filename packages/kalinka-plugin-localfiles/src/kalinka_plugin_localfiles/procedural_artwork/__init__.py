"""Standalone deterministic procedural album artwork generator.

Generates abstract album covers from album metadata using only Pillow and
NumPy.  Different releases of the same album (original / remaster / deluxe)
share a family identity and stay visually related; edition-level differences
are restrained.  See README.md in this package for the design overview.
"""

from .generator import GENERATOR_VERSION, MAX_SIZE, MIN_SIZE, ProceduralArtworkGenerator
from .models import (
    AlbumArtworkInput,
    ArtworkError,
    ArtworkParameters,
    FileWriteError,
    InvalidEmbeddingError,
    InvalidInputError,
    InvalidSizeError,
    RenderError,
    UnsupportedFormatError,
)

__all__ = [
    "GENERATOR_VERSION",
    "MIN_SIZE",
    "MAX_SIZE",
    "ProceduralArtworkGenerator",
    "AlbumArtworkInput",
    "ArtworkParameters",
    "ArtworkError",
    "InvalidInputError",
    "InvalidSizeError",
    "InvalidEmbeddingError",
    "UnsupportedFormatError",
    "RenderError",
    "FileWriteError",
]
