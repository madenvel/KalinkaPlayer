"""Shared name & path normalization for artist/album identity.

Three concerns live here:

- ``normalize_for_id``: aggressive normalization used only as input to the
  ID hash. Collapses surface-level differences so "- Roxy Music" and
  "Roxy Music", or "Jean-Michel Jarre" and "Jean Michel Jarre", produce the
  same stable ID. The output is never shown to a user.

- ``clean_display_name``: light cleanup of what gets stored in
  ``artists.name`` / ``albums.title``. Strips leading/trailing punctuation
  and collapses internal whitespace runs, but leaves the rest of the
  user-visible string untouched (hyphens, case, diacritics preserved).

- ``album_folder_for_path``: maps a track's file path to the directory
  that represents its album. The immediate parent is the album folder,
  unless that parent looks like a disc subdir (``CD1`` / ``Disc 2``) in
  which case we walk up one more level. This is what makes the album ID
  folder-bounded: same album in two quality folders produces two
  distinct album IDs, while ``Disc 1`` and ``Disc 2`` of one set merge.

Keeping these in one place avoids drift between the indexer's
``id_generator.py`` and the enricher's copy.
"""

from __future__ import annotations

import os
import re
import unicodedata

_LEADING_TRAILING_PUNCT_RE = re.compile(r"^[\-_.,/ \t]+|[\-_.,/ \t]+$")
_WHITESPACE_RUN_RE = re.compile(r"\s+")
_PUNCT_FOR_ID_RE = re.compile(r"[\-_./]")
_NON_WORD_RE = re.compile(r"[^\w\s]")
_DISC_SUBDIR_RE = re.compile(r"^(cd|disc|disk)[\s\-_]*\d+$", re.IGNORECASE)


def clean_display_name(name: str) -> str:
    """Sanitize a name before it's stored in the DB.

    Strips leading/trailing ``-_.,/`` and whitespace, and collapses internal
    whitespace runs. Preserves case, hyphens, and diacritics so the stored
    string still matches what the user sees in their file tags.
    """
    if not name:
        return ""
    cleaned = _LEADING_TRAILING_PUNCT_RE.sub("", name)
    cleaned = _WHITESPACE_RUN_RE.sub(" ", cleaned).strip()
    return cleaned


def album_folder_for_path(file_path: str) -> str:
    """Pick the directory that represents this file's album.

    Returns the file's parent dir, except when that parent is a disc
    subdir (``CD1`` / ``Disc 2`` / ``Disk-3``), in which case the
    grandparent is returned so multi-disc rips collapse into one album.

    Used as the folder component of the album ID, so files in
    ``/Music/RAM 24bit`` and ``/Music/RAM 16bit`` end up in different
    albums even when their tag album-title is identical, while
    ``/.../Album/Disc 1`` and ``/.../Album/Disc 2`` end up in the same
    one.
    """
    if not file_path:
        return ""
    parent = os.path.dirname(file_path)
    name = os.path.basename(parent)
    if _DISC_SUBDIR_RE.match(name):
        return os.path.dirname(parent)
    return parent


def normalize_for_id(name: str) -> str:
    """Aggressive normalization used only for ID hashing.

    Steps:
      1. Unicode NFKD, strip combining marks (so "Beyoncé" hashes like "Beyonce")
      2. Lowercase
      3. Replace ``-_./`` with space (so "Jean-Michel Jarre" hashes like "Jean Michel Jarre")
      4. Drop remaining non-word characters
      5. Collapse whitespace runs and strip
    """
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = n.lower()
    n = _PUNCT_FOR_ID_RE.sub(" ", n)
    n = _NON_WORD_RE.sub("", n)
    n = _WHITESPACE_RUN_RE.sub(" ", n).strip()
    return n
