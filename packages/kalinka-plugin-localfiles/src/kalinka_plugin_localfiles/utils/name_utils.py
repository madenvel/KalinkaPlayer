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

import html
import os
import re
import unicodedata

import ftfy
from collections.abc import Iterable
from pathlib import Path
from typing import Optional

_LEADING_TRAILING_PUNCT_RE = re.compile(r"^[\-_.,/ \t]+|[\-_.,/ \t]+$")
_WEB_ENTITY_RE = re.compile(r"&(?:amp|lt|gt|quot|apos|nbsp|#\d{1,7}|#[xX][0-9A-Fa-f]{1,6});")
_DOTTED_ABBREV_RE = re.compile(r"(?<=\w)\.(?=[^\W\d_]{2})")
_WHITESPACE_RUN_RE = re.compile(r"\s+")
_PUNCT_FOR_ID_RE = re.compile(r"[\-_./]")
_NON_WORD_RE = re.compile(r"[^\w\s]")
_DISC_SUBDIR_RE = re.compile(r"^(cd|disc|disk)[\s\-_]*\d+$", re.IGNORECASE)


def unescape_web_entities(text: str) -> str:
    """One pass of HTML-entity unrolling for tag text mangled by web tools
    ("Simon &amp; Garfunkel" -> "Simon & Garfunkel").

    Only semicolon-terminated entities from the common mangling set are
    converted — full ``html.unescape`` would also rewrite HTML5 legacy forms
    without a semicolon ("&notabene" -> "¬abene").
    """
    if not text or "&" not in text:
        return text
    return _WEB_ENTITY_RE.sub(lambda m: html.unescape(m.group(0)), text)


def space_dotted_abbreviations(name: str) -> str:
    """Insert the missing space after an abbreviating period for search
    queries ("В.Цой" -> "В. Цой", "J.S.Bach" -> "J.S. Bach"). Dotted
    acronyms ("R.E.M.") and ellipsis prefixes ("...And Justice") are left
    alone: the space lands only between a word character and a following
    token of two or more letters.
    """
    if not name or "." not in name:
        return name
    return _DOTTED_ABBREV_RE.sub(". ", name)


def _script_of(ch: str) -> str:
    """Unicode script prefix of a letter ("CYRILLIC", "GREEK", …); unnamed
    characters count as LATIN so they weigh against a repair."""
    name = unicodedata.name(ch, "")
    return name.split(" ")[0] if name else "LATIN"


def _mojibake_bytes(text: str) -> Optional[bytes]:
    """The original byte sequence of a latin-1/cp1252-misread string, or
    None when the text holds genuine non-latin1 unicode (nothing to
    recover)."""
    for encoding in ("latin-1", "cp1252"):
        try:
            return text.encode(encoding)
        except UnicodeEncodeError:
            continue
    return None


def repair_mojibake(text: str, legacy_encoding: Optional[str] = None) -> str:
    """Undo tag text misread in the wrong encoding, conservatively.

    Tier 1, unconditional: ftfy repairs mojibake whose underlying bytes are
    UTF-8 ("BjÃ¶rk" -> "Björk", double-encoded "BjÃƒÂ¶rk" too) — the class
    that is self-evident from byte structure alone.

    Tier 2, only with a configured ``legacy_encoding`` (cp1251, cp1253, …):
    a single-byte codepage read as latin-1 ("ÐÓÊÈ ÂÂÅÐÕ" -> "РУКИ ВВЕРХ").
    ftfy deliberately does not touch this class: the codepage cannot be
    detected from a short name — the same bytes decode "coherently" in
    every full 8-bit codepage — so it must be declared, and the repair
    fires only when the text is dominated by high-range letters and the
    result lands in one non-Latin script. Accented western names
    ("Mötley Crüe") never qualify.
    """
    if not text:
        return text
    fixed = ftfy.fix_encoding(text)
    if fixed != text:
        return fixed
    if not legacy_encoding:
        return text
    raw = _mojibake_bytes(text)
    if raw is None:
        return text
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return text
    high = sum(1 for c in letters if 0xC0 <= ord(c) <= 0xFF)
    if high / len(letters) < 0.6:
        return text
    try:
        candidate = raw.decode(legacy_encoding)
    except (UnicodeDecodeError, LookupError):
        return text
    cand_scripts = [_script_of(c) for c in candidate if c.isalpha()]
    non_latin = set(cand_scripts) - {"LATIN"}
    if not cand_scripts or len(non_latin) != 1:
        return text
    if sum(s in non_latin for s in cand_scripts) / len(cand_scripts) < 0.9:
        return text
    return candidate


def repair_tag_text(text: str, legacy_encoding: Optional[str] = None) -> str:
    """The full tag-repair chain — mojibake, web entities, dotted
    abbreviations — for any name or title headed for the library."""
    return space_dotted_abbreviations(
        unescape_web_entities(repair_mojibake(text, legacy_encoding))
    )


def clean_display_name(name: str) -> str:
    """Sanitize a name before it's stored in the DB.

    Unrolls web-mangled HTML entities, strips leading/trailing ``-_.,/`` and
    whitespace, and collapses internal whitespace runs. Preserves case,
    hyphens, and diacritics so the stored string still matches what the user
    sees in their file tags.
    """
    if not name:
        return ""
    cleaned = unescape_web_entities(name)
    cleaned = _LEADING_TRAILING_PUNCT_RE.sub("", cleaned)
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


def expand_music_folders(folders: Iterable[str]) -> list[str]:
    """Expand ``~`` and resolve each configured music folder to a canonical
    absolute path. The indexer stores file paths derived from these (so they
    are already canonical); resolving the roots the same way lets
    :func:`path_within_roots` compare them directly.
    """
    resolved: list[str] = []
    for folder in folders:
        if not folder:
            continue
        try:
            resolved.append(str(Path(folder).expanduser().resolve()))
        except (OSError, RuntimeError, ValueError):
            continue
    return resolved


def path_within_roots(file_path: str, roots: Iterable[str]) -> bool:
    """Return True if ``file_path`` lives inside one of ``roots``.

    ``roots`` are expected to be canonical absolute paths (see
    :func:`expand_music_folders`). Membership is tested per path component,
    so ``/Music`` does not match a sibling ``/Music2``. A path equal to a
    root is considered inside it.

    This is the access boundary for the local-files module: only files under
    a configured music folder may be indexed or played. When the folder
    config changes, entries that fall outside the new roots are no longer
    accessible and must be purged / refused.
    """
    if not file_path:
        return False
    try:
        target = Path(file_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    for root in roots:
        try:
            root_path = Path(root)
        except (OSError, ValueError):
            continue
        if target == root_path or root_path in target.parents:
            return True
    return False


def fold_diacritics(name: str) -> str:
    """Strip diacritics while preserving case, spacing, and punctuation.

    NFKD-decompose and drop combining marks so "Női Kabát" folds to
    "Noi Kabat". Unlike :func:`normalize_for_id` the surface form is
    otherwise intact; used by the searcher re-rank to compare an unaccented
    query against accented metadata.
    """
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name)
    return "".join(c for c in n if not unicodedata.combining(c))


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
