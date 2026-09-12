"""Track metadata read out of a file's path by a bundled CRF.

The one import target for both consumers — the indexer, for scan-time track
numbers, and the enricher's filesystem fallback, for everything a file with no
usable tags can still tell us. See ``README.md`` for the bundled model and
``_vendor/README.md`` for the copied runtime.
"""

from __future__ import annotations

from typing import Optional

from ..clustering.classify import is_various_artists_name
from .assembler import (
    MIN_SCORE,
    PathMetadata,
    assemble,
    build_view,
    names_a_vinyl_side,
)
from .parser import FilenameModel, get_parser

__all__ = [
    "MIN_SCORE",
    "FilenameModel",
    "PathMetadata",
    "get_parser",
    "names_a_vinyl_side",
    "parse_music_path",
]


def parse_music_path(
    file_path: str, music_root: Optional[str]
) -> Optional[PathMetadata]:
    """What ``file_path`` says about its track, or None when it says nothing.

    ``None`` covers every reason there is no answer — the path lies outside
    the configured music roots, the model could not be loaded, or the parse
    itself failed — because every caller treats them the same way.
    """
    view = build_view(file_path, music_root)
    if not view:
        return None
    result = get_parser().parse(view)
    if result is None:
        return None
    return assemble(view, result, is_placeholder_artist=is_various_artists_name)
