"""Claims resolution (Phase 2c, §7).

A field's resolved value is the winner of its claims: higher tier wins; within
a tier a fixed per-field source precedence decides; a tie is broken in favour
of the value currently resolved (incumbency), so display never churns.

Numeric match scores never enter here — they are how a source *earns* a tier
inside its own accept/reject decision and stay out of cross-source comparison.
This module is a pure function over claims; the DB wiring lives in the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

#: Tier for a value read from the file's own tags.
OBSERVED = "observed"
#: Tier for a value read off the path — a guess, and outranked by everything.
GUESSED = "guessed"

#: Source of a value read from the file's own tags.
TAG_CONSENSUS = "tag_consensus"
#: Sources a path-derived value is attributed to.
FILENAME = "filename"
FOLDER_NAME = "folder_name"

#: The sources whose values this library derives for itself and may re-derive.
#: Anything else was resolved by a source that outranks a folder name, so
#: rebuilding it from the folder would revert that source on the next scan.
LOCALLY_DERIVED = (TAG_CONSENSUS, FOLDER_NAME)

# Tier ordering (§7). pinned = user said so; guessed = last-resort fallback.
TIER_RANK = {
    "pinned": 5,
    "verified": 4,
    "observed": 3,
    "inferred": 2,
    "guessed": 1,
}

# Within-tier source precedence. Two profiles cover the fields we resolve:
#   local_first  — display identity (titles, artist): tags read from the files
#                  beat a folder name, which beats any external match. A fuzzy
#                  external match is inferred and must never silently replace a
#                  locally observed title/artist (§7 field policy).
#   external_first — origin/era facts (country, year, language, genre): a
#                  release/artist database is authoritative over local tags,
#                  and library inference sits above raw tags for these.
# `user` is always highest; filename/procedural are always last.
_LOCAL_FIRST = (
    "user",
    "tag_consensus",
    "folder_name",
    "library_inference",
    "musicbrainz",
    "deezer",
    "wikidata",
    "acoustid",
    "filename",
    "procedural_artwork",
)
_EXTERNAL_FIRST = (
    "user",
    "musicbrainz",
    "deezer",
    "wikidata",
    "library_inference",
    "tag_consensus",
    "folder_name",
    "acoustid",
    "filename",
)

_EXTERNAL_FIRST_FIELDS = frozenset(
    {
        "country",
        "area",
        "year",
        "original_year",
        "recording_year",
        "language",
        "genre",
    }
)


@dataclass(frozen=True)
class Claim:
    field: str
    # str for identity/text fields; int for numeric origin/era fields
    # (year, original_year). Resolution compares by (tier, source), never by
    # the value itself, so the mixed type is safe here.
    value: str | int
    source: str  # e.g. "tag_consensus" or "musicbrainz:0d7f…"
    tier: str
    evidence_ref: Optional[str] = None


def _source_base(source: str) -> str:
    """Strip the id suffix: "musicbrainz:0d7f…" -> "musicbrainz"."""
    return source.split(":", 1)[0]


def _precedence(field: str) -> tuple:
    return _EXTERNAL_FIRST if field in _EXTERNAL_FIRST_FIELDS else _LOCAL_FIRST


def _source_rank(field: str, source: str) -> int:
    """Higher rank = higher precedence. Unknown sources rank below all known
    ones (but above nothing), so an unrecognised emitter never outranks a
    known one merely by being unlisted."""
    order = _precedence(field)
    base = _source_base(source)
    if base in order:
        # Invert index so earlier-in-tuple = higher rank.
        return len(order) - order.index(base)
    return 0


def _score(claim: Claim) -> tuple:
    return (TIER_RANK.get(claim.tier, 0), _source_rank(claim.field, claim.source))


def resolve_field(
    claims: Iterable[Claim], current_value: Optional[str | int] = None
) -> Optional[Claim]:
    """Pick the winning claim for a single field.

    `current_value` is the value already resolved (if any); it wins ties so a
    display value only changes when a strictly better claim appears.
    """
    best: Optional[Claim] = None
    best_score: Optional[tuple] = None
    for claim in claims:
        score = _score(claim)
        if best is None or score > best_score:
            best, best_score = claim, score
        elif score == best_score:
            # Tie: incumbency — keep whichever equals the current value.
            if (
                current_value is not None
                and claim.value == current_value
                and best.value != current_value
            ):
                best, best_score = claim, score
    return best


def _norm(s: str) -> str:
    return " ".join((s or "").split()).casefold()


def resolve_display_name(
    local_value: str,
    external: Iterable[Claim],
    field: str = "name",
    local_source: str = TAG_CONSENSUS,
    local_tier: str = OBSERVED,
) -> Claim:
    """Resolve a display-identity field (artist ``name`` / album ``title``)
    under the §7 rule that a fuzzy external match may re-format but not replace
    it.

    An observed local value wins, except: a verified/pinned external (direct
    identifier or user confirmation) replaces it outright; otherwise a same-
    value external (equal up to case/whitespace) supplies the canonical surface
    form ("THE BEATLES" -> "The Beatles").

    ``local_source``/``local_tier`` say where the local value actually came
    from. They default to a tag read out of the file, which is the case §7
    protects; a value read off the *path* is a ``guessed`` claim from
    ``filename``/``folder_name`` and does lose to a fuzzy external match —
    correcting a filename's typos is the whole point of consulting one.

    ``field`` labels the synthesized local claim; both name and title resolve
    under the same local-first precedence, so it is cosmetic, but keeping it
    accurate makes the returned/recorded provenance correct per field.
    """
    local = Claim(field, local_value, local_source, local_tier)
    external = list(external)

    # Order-independent: pick by the resolver's (tier, source-precedence) score,
    # not by input order, so multiple external claims resolve deterministically.
    strong = [c for c in external if TIER_RANK.get(c.tier, 0) >= TIER_RANK["verified"]]
    if strong:
        return max(strong, key=_score)

    matching = [c for c in external if _norm(c.value) == _norm(local_value)]
    if matching:
        return max(matching, key=_score)
    # Never None — the local claim is always one of the candidates.
    return resolve_field([local, *external], current_value=local_value) or local


def resolve_entity(
    claims: Iterable[Claim], current: Optional[dict] = None
) -> List[Claim]:
    """Resolve every field present in `claims`, returning one winning Claim per
    field. `current` maps field -> currently-resolved value for incumbency."""
    current = current or {}
    by_field: dict[str, List[Claim]] = {}
    for claim in claims:
        by_field.setdefault(claim.field, []).append(claim)
    winners = []
    for field, field_claims in by_field.items():
        winner = resolve_field(field_claims, current.get(field))
        if winner is not None:
            winners.append(winner)
    return winners
