"""Shared V/A-compilation and disc-suffix helpers.

Single source of truth for the folder-classification logic that used to live
only inside the indexer's orphan_va pass. Both the indexer and the clustering
planner import from here so the two cannot drift.
"""

import os
import re
from typing import Optional

# A folder's tracks coalesce into a single V/A compilation album when they
# span at least this many distinct artists AND at least this fraction of
# tracks have unique artists. Both, so a mistagged album (Abbey Road with a
# couple of wrong-artist tags) doesn't flip to V/A.
VA_MIN_DISTINCT_ARTISTS = 4
VA_MIN_ARTIST_UNIQUENESS = 0.5

VARIOUS_ARTISTS_ID = "various_artists"

# Dumping-ground / structural folder names — not compilations or artists.
GENERIC_FOLDER_RE = re.compile(
    r"^(music|musik|audio|downloads?|mp3s?|tracks?|songs?|various|"
    r"streamed_music|unused|unsorted|sorted|misc|miscellaneous|temp|tmp|"
    r"incoming|untitled|new folder|to ?sort|todo|.*\bmix(?:es)?\b.*)$",
    re.IGNORECASE,
)
# Bare disc/volume folder names that need the parent dir for a real title.
BARE_DISC_RE = re.compile(
    r"^(cd[-_ ]?\d+|disc\s*\d+|disk\s*\d+|volume\s*\d+|vol\.?\s*\d+)$",
    re.IGNORECASE,
)
VA_PREFIX_RE = re.compile(r"^(va|various artists?)\s*[-–—]\s*", re.IGNORECASE)
# A trailing "(Disc 2)" / "CD1" / "Vol. 3" on an album title.
_DISC_SUFFIX_RE = re.compile(
    r"[\s\-–—_([]+(?:cd|disc|disk|volume|vol\.?)\s*\d+\s*[)\]]?\s*$",
    re.IGNORECASE,
)


def is_va_folder(distinct_artists: int, n_tracks: int) -> bool:
    """Whether a folder qualifies as various-artists by the count thresholds."""
    if n_tracks <= 0:
        return False
    return (
        distinct_artists >= VA_MIN_DISTINCT_ARTISTS
        and (distinct_artists / n_tracks) >= VA_MIN_ARTIST_UNIQUENESS
    )


def compilation_title(folder: str) -> Optional[str]:
    """Album title for a V/A folder, or None if it's a generic dump.

    A folder explicitly marked ``VA -`` / ``Various Artists -`` is a declared
    compilation and bypasses the generic-dump heuristic.
    """
    name = os.path.basename(folder).strip()
    title = VA_PREFIX_RE.sub("", name).strip()
    explicit_va = title != name
    if not title:
        return None
    if not explicit_va and GENERIC_FOLDER_RE.match(title):
        return None
    if BARE_DISC_RE.match(title):
        parent = os.path.basename(os.path.dirname(folder)).strip()
        title = f"{parent} {title}".strip() if parent else title
    return title or None


def strip_artist_prefix(title: str, artist_name: str) -> str:
    """Drop a leading artist name from a title (``Ratatat Remixes`` ->
    ``Remixes``). Only at a separator/word boundary, so ``AB`` does not
    corrupt ``ABBA Gold``."""
    if title.lower().startswith(artist_name.lower()):
        rest = title[len(artist_name):]
        if not rest or rest[0] in " -–—":
            return rest.lstrip(" -–—") or title
    return title


def strip_disc_suffix(title: str) -> str:
    """Remove a trailing disc/volume marker so multi-disc-tagged sets share an
    album key (``Album (Disc 1)`` and ``Album (Disc 2)`` -> ``Album``)."""
    if not title:
        return title
    return _DISC_SUFFIX_RE.sub("", title).strip() or title
