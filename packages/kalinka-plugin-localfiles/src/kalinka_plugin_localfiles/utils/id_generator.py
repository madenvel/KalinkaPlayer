"""Canonical ID generators for artists, albums, tracks, playlists.

There used to be two copies (one in ``indexer/``, one in ``enricher/``)
that drifted independently. Everything now imports from here so the
two halves of the pipeline cannot disagree on what ID a given
(artist_name, album_title, file_path) collapses to.
"""

from __future__ import annotations

import hashlib
import uuid

from .name_utils import normalize_for_id


def generate_artist_id(artist_name: str) -> str:
    """Stable artist ID derived from the normalized name.

    "- Roxy Music", "Roxy Music", and "Roxy Music" with diacritics
    all collapse to the same ID — see ``normalize_for_id``.
    """
    if not artist_name or artist_name == "Unknown Artist":
        return "unknown_artist"
    key = normalize_for_id(artist_name)
    if not key:
        return "unknown_artist"
    return f"artist_{hashlib.md5(key.encode('utf-8')).hexdigest()[:16]}"


def generate_album_id(album_title: str, album_folder: str) -> str:
    """Stable album ID keyed on folder + normalized title.

    The folder is part of the key so quality variants in sibling
    directories (e.g. 16/44 and 24/96 rips) get distinct IDs, while
    a track mistagged with a different artist inside the album
    folder still collapses into the same album. Disc subdirs
    (``CD1`` / ``Disc 2``) should be stripped from ``album_folder``
    before this is called — see ``album_folder_for_path``.
    """
    if not album_title or album_title == "Unknown Album":
        return "unknown_album"
    title_key = normalize_for_id(album_title)
    if not title_key:
        return "unknown_album"
    folder_key = album_folder or ""
    payload = f"{folder_key}\0{title_key}".encode("utf-8")
    return f"album_{hashlib.md5(payload).hexdigest()[:16]}"


def generate_track_id(file_path: str) -> str:
    """Stable track ID derived from the file path."""
    return f"track_{hashlib.md5(file_path.encode('utf-8')).hexdigest()[:16]}"


def generate_playlist_id(name: str, created_by: str) -> str:
    """Stable playlist ID from name + creator."""
    payload = f"{name.lower()}{created_by}".encode("utf-8")
    return f"playlist_{hashlib.md5(payload).hexdigest()[:16]}"


def generate_playlist_track_id() -> str:
    """Unique ID for a playlist-track entry; allows same track multiple times."""
    return f"ptrack_{uuid.uuid4().hex[:16]}"
