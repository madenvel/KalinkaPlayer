"""Deterministic album-family and edition identity.

Two separate identity layers keep releases of the same album visually
related:

* **family identity** — which album this is (release-group MBID when
  available, otherwise normalized artist + base title + track signature).
  It selects template, composition, palette family and broad style.
* **edition identity** — which release/edition of the album this is.  It
  only contributes restrained, bounded variation on top of the family look.

All seeds are 64-bit values derived with BLAKE2b (never Python ``hash()``),
with distinct personalization strings for the family and edition domains.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass

import numpy as np

from .models import AlbumArtworkInput, InvalidInputError

# Personalization values keep family / edition / semantic hash domains apart
# (blake2b allows up to 16 bytes).
_PERSON_FAMILY = b"kalinka-art-fam"
_PERSON_EDITION = b"kalinka-art-edn"
_PERSON_SEMANTIC = b"kalinka-art-sem"

_SEED_MASK = (1 << 64) - 1

# Number of leading tracks that participate in the canonical track-list
# signature.  Capping keeps the fallback family identity stable when a
# deluxe/bonus release appends extra tracks after the base track list.
TRACK_SIGNATURE_LIMIT = 12

# Edition variation strengths (fraction of the family->edition interpolation).
# Tunable, but release-level variation must stay subordinate to family
# identity, so keep these well below 0.5.
EDITION_STRENGTHS: dict[str, float] = {
    "original": 0.0,
    "remaster": 0.05,
    "mono_stereo": 0.07,
    "alternate": 0.08,
    "anniversary": 0.10,
    "special": 0.10,
    "bonus": 0.12,
    "deluxe": 0.14,
}

# Controlled edition-marker vocabulary.  A trailing "(...)", "[...]" or
# " - ..." suffix is stripped only when its entire content is made of these
# phrases; arbitrary parenthesized content ("(Live at Wembley)") is kept.
_YEAR = r"(?:19|20)\d{2}"
_MARKER_PATTERNS = (
    rf"{_YEAR}\s+remaster(?:ed)?",
    rf"remaster(?:ed)?(?:\s+{_YEAR})?(?:\s+(?:version|edition))?",
    r"(?:\d{1,3}(?:st|nd|rd|th)\s+)?anniversary(?:\s+(?:edition|version))?",
    r"deluxe(?:\s+(?:edition|version))?",
    r"expanded(?:\s+(?:edition|version))?",
    r"special\s+edition",
    r"collector'?s\s+edition",
    r"bonus\s+tracks?(?:\s+(?:edition|version))?",
    r"mono",
    r"stereo",
)
_MARKER_ALT = "(?:" + "|".join(_MARKER_PATTERNS) + ")"
_MARKER_GROUP_RE = re.compile(
    rf"^\s*{_MARKER_ALT}(?:\s*[,/&+;]?\s*{_MARKER_ALT})*\s*$", re.IGNORECASE
)
_TRAILING_PAREN_RE = re.compile(r"\s*[(\[]([^()\[\]]*)[)\]]\s*$")
_TRAILING_DASH_RE = re.compile(r"\s+[-–—]\s+([^-–—]+?)\s*$")

# Keyword -> edition kind, checked against stripped marker text.
_KIND_KEYWORDS = (
    ("deluxe", "deluxe"),
    ("expanded", "deluxe"),
    ("bonus", "bonus"),
    ("anniversary", "anniversary"),
    ("special", "special"),
    ("collector", "special"),
    ("remaster", "remaster"),
    ("mono", "mono_stereo"),
    ("stereo", "mono_stereo"),
)


@dataclass(frozen=True)
class AlbumIdentity:
    """Resolved identity: family and edition seeds plus edition class."""

    family_key: str
    family_seed: int
    edition_seed: int
    edition_kind: str
    edition_strength: float
    base_title: str
    normalized_full_title: str


def normalize_text(value: str) -> str:
    """Normalize text for identity matching.

    Applies NFKD + diacritics stripping, casefolding, ``&`` -> ``and``, and
    collapses everything that is not a word character into single spaces.
    Non-Latin scripts are preserved.
    """
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"[\W_]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def strip_edition_markers(title: str) -> tuple[str, tuple[str, ...]]:
    """Split a full album title into (base title, stripped marker texts).

    Only trailing parenthesized/bracketed groups or " - " suffixes whose
    entire content matches the controlled edition-marker vocabulary are
    removed, repeatedly, so "Album (Deluxe Edition) [2019 Remaster]" reduces
    to "Album".  Meaningful suffixes like "(Live at Wembley)" are preserved.
    """
    base = title.strip()
    markers: list[str] = []
    while True:
        match = _TRAILING_PAREN_RE.search(base)
        if match and _MARKER_GROUP_RE.match(match.group(1)):
            markers.append(match.group(1).strip())
            base = base[: match.start()].strip()
            continue
        match = _TRAILING_DASH_RE.search(base)
        if match and _MARKER_GROUP_RE.match(match.group(1)):
            markers.append(match.group(1).strip())
            base = base[: match.start()].strip()
            continue
        break
    return base, tuple(markers)


def classify_edition(markers: tuple[str, ...], release_id: str | None) -> tuple[str, float]:
    """Map stripped edition markers to an (edition kind, strength) pair.

    With no markers, a distinct ``release_id`` classifies as an unknown
    alternate release; otherwise the release is treated as the original.
    When several markers are present the strongest one wins.
    """
    kinds: set[str] = set()
    for marker in markers:
        lowered = marker.casefold()
        for keyword, kind in _KIND_KEYWORDS:
            if keyword in lowered:
                kinds.add(kind)
    if kinds:
        kind = max(kinds, key=lambda k: EDITION_STRENGTHS[k])
        return kind, EDITION_STRENGTHS[kind]
    if release_id:
        return "alternate", EDITION_STRENGTHS["alternate"]
    return "original", EDITION_STRENGTHS["original"]


def canonical_track_signature(
    track_titles: tuple[str, ...], limit: int = TRACK_SIGNATURE_LIMIT
) -> str:
    """Build a canonical signature from the leading normalized track titles.

    Limiting to the first ``limit`` tracks makes the signature resistant to
    bonus tracks appended by deluxe/expanded editions (as long as the base
    album reaches the cap; shorter albums rely on the base-title match).
    """
    normalized = [normalize_text(t) for t in track_titles[:limit]]
    return "\x1f".join(t for t in normalized if t)


def stable_hash(parts: tuple, person: bytes) -> int:
    """Stable 64-bit hash of a tuple of parts within a given hash domain."""
    digest = hashlib.blake2b(digest_size=8, person=person)
    for part in parts:
        data = str(part).encode("utf-8")
        digest.update(len(data).to_bytes(4, "big"))
        digest.update(data)
    return int.from_bytes(digest.digest(), "big")


def semantic_hash(parts: tuple) -> int:
    """Stable 64-bit hash in the semantic (embedding projection) domain."""
    return stable_hash(parts, _PERSON_SEMANTIC)


def rng_for(seed: int, domain: int) -> np.random.Generator:
    """Deterministic RNG stream for a (seed, domain) pair.

    Always the modern ``Generator(PCG64(...))`` API; never touches numpy's
    global random state.  Distinct domains give independent streams from the
    same identity seed.
    """
    return np.random.Generator(
        np.random.PCG64(np.random.SeedSequence([seed & _SEED_MASK, domain]))
    )


def _validated_tracks(album: AlbumArtworkInput) -> tuple[str, ...]:
    tracks = album.track_titles
    if tracks is None:
        return ()
    if not isinstance(tracks, (tuple, list)):
        raise InvalidInputError(
            f"track_titles must be a tuple of str, got {type(tracks).__name__}"
        )
    for item in tracks:
        if not isinstance(item, str):
            raise InvalidInputError(
                f"track_titles entries must be str, got {type(item).__name__}"
            )
    return tuple(tracks)


def validate_album_input(album: AlbumArtworkInput) -> None:
    """Validate metadata field types, raising :class:`InvalidInputError`."""
    if not isinstance(album, AlbumArtworkInput):
        raise InvalidInputError(
            f"album must be AlbumArtworkInput, got {type(album).__name__}"
        )
    for field in ("artist", "title"):
        value = getattr(album, field)
        if not isinstance(value, str):
            raise InvalidInputError(f"{field} must be str, got {type(value).__name__}")
    for field in ("genre", "release_group_id", "release_id", "embedding_version"):
        value = getattr(album, field)
        if value is not None and not isinstance(value, str):
            raise InvalidInputError(
                f"{field} must be str or None, got {type(value).__name__}"
            )
    if not album.artist.strip() and not album.title.strip():
        raise InvalidInputError("at least one of artist or title must be non-empty")
    _validated_tracks(album)


def build_identity(album: AlbumArtworkInput, generator_version: int) -> AlbumIdentity:
    """Resolve family + edition identity for an album input.

    Family precedence: MusicBrainz release-group MBID, else fallback key from
    normalized artist + normalized base title + canonical track signature.
    Output size participates in neither seed.
    """
    validate_album_input(album)
    tracks = _validated_tracks(album)

    base_title, markers = strip_edition_markers(album.title)
    normalized_base = normalize_text(base_title)
    normalized_full = normalize_text(album.title)
    track_sig = canonical_track_signature(tracks)

    rgid = (album.release_group_id or "").strip()
    if rgid:
        family_key = f"rg:{rgid.casefold()}"
    else:
        family_key = f"meta:{normalize_text(album.artist)}\x1f{normalized_base}\x1f{track_sig}"

    family_seed = stable_hash((generator_version, family_key), _PERSON_FAMILY)
    edition_seed = stable_hash(
        (generator_version, family_seed, normalized_full, album.release_id or "", track_sig),
        _PERSON_EDITION,
    )
    edition_kind, edition_strength = classify_edition(markers, album.release_id)
    return AlbumIdentity(
        family_key=family_key,
        family_seed=family_seed,
        edition_seed=edition_seed,
        edition_kind=edition_kind,
        edition_strength=edition_strength,
        base_title=base_title,
        normalized_full_title=normalized_full,
    )
