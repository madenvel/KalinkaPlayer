"""Shared name normalization for artist/album identity.

Two distinct concerns live here:

- ``normalize_for_id``: aggressive normalization used only as input to the
  ID hash. Collapses surface-level differences so "- Roxy Music" and
  "Roxy Music", or "Jean-Michel Jarre" and "Jean Michel Jarre", produce the
  same stable ID. The output is never shown to a user.

- ``clean_display_name``: light cleanup of what gets stored in
  ``artists.name`` / ``albums.title``. Strips leading/trailing punctuation
  and collapses internal whitespace runs, but leaves the rest of the
  user-visible string untouched (hyphens, case, diacritics preserved).

Keeping these in one place avoids drift between the indexer's
``id_generator.py`` and the enricher's copy.
"""

from __future__ import annotations

import re
import unicodedata

_LEADING_TRAILING_PUNCT_RE = re.compile(r"^[\-_.,/ \t]+|[\-_.,/ \t]+$")
_WHITESPACE_RUN_RE = re.compile(r"\s+")
_PUNCT_FOR_ID_RE = re.compile(r"[\-_./]")
_NON_WORD_RE = re.compile(r"[^\w\s]")


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
