"""
"BEST MATCH" assembly for Kalinka full-text search.

Takes a flat list of scored library entities (artists, albums, tracks) and
produces a small, high-confidence ordered set answering *navigational* intent
("I know what this is called, take me to it").  The result renders as a single
flat "BEST MATCH" block at the very top of search results, above the AI /
semantic suggestions.  This module is purely about the FTS/literal side; the
semantic sections are assembled elsewhere and are unaffected.

Algorithm (executed in this exact order — see assemble_best_match):
    1. Score every candidate against the query; discard score < RAPIDFUZZ_CUTOFF.
    2. Sort surviving entities (all types mixed) by score descending; keep top
       MAX_RESULTS.
    3. Remove redundancy on the truncated list, strictly-greater comparisons:
         a. album dominates its tracks (album.score > track.score)
         b. artist dominates its tracks/albums (artist.score > entity.score)
    4. Return the remaining entities, still score-descending (0..MAX_RESULTS).

The truncate-before-dedup ordering (step 2 before step 3) is deliberate: a
strong-but-redundant entity can crowd out a weaker survivor, leaving fewer than
MAX_RESULTS items.  That is intended; we do not backfill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz

from ..utils.name_utils import fold_diacritics

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

# Inclusion threshold (rapidfuzz 0-100); a weak top "BEST MATCH" is worse than
# none. 88: the case-folded scorer lands real matches >=90 but a query sharing
# one word with a title ~85. Overridden by searcher.best_match_min_fuzz_score.
RAPIDFUZZ_CUTOFF: float = 88.0

# Maximum number of entities in the BEST MATCH block.  Overridden at runtime by
# config (searcher.best_match_max_results).
MAX_RESULTS: int = 6

# Scorer applied to (query, entity.name). WRatio is robust to word-order and
# partial matches ("piano guys" vs "The Piano Guys").
SCORER = fuzz.WRatio


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Entity:
    """A library entity (artist / album / track); ``score`` is filled in by
    assemble_best_match."""

    id: str
    type: str  # "artist" | "album" | "track"
    name: str
    score: float = 0.0
    album_id: Optional[str] = None  # tracks only
    artist_id: Optional[str] = None  # tracks and albums


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def assemble_best_match(
    candidates: list[Entity],
    query: str,
    *,
    cutoff: float = RAPIDFUZZ_CUTOFF,
    max_results: int = MAX_RESULTS,
) -> list[Entity]:
    """Score, cut off, sort, truncate to ``max_results``, then remove album/
    artist-dominated redundancies.

    Returns 0..``max_results`` entities, score-descending, exactly as produced
    (no re-sort or regroup by type).  An empty list means the UI renders no
    BEST MATCH section.  ``cutoff`` / ``max_results`` default to the module
    constants and are overridden from config by the search pipeline.
    """
    # Score candidates, discard below cutoff. Fold diacritics + case so matching
    # is case-insensitive and ASCII queries still match accented names.
    folded_query = fold_diacritics(query).casefold()
    survivors: list[Entity] = []
    for entity in candidates:
        entity.score = SCORER(folded_query, fold_diacritics(entity.name).casefold())
        if entity.score >= cutoff:
            survivors.append(entity)

    # Sort by score, keep the top ``max_results`` — the list the redundancy
    # rules below operate on.
    survivors.sort(key=lambda e: e.score, reverse=True)
    the_list = survivors[:max_results]

    # Remove redundancy. Index by id+type so a track and its same-named
    # album/artist never collide.
    albums_by_id = {e.id: e for e in the_list if e.type == "album"}
    artists_by_id = {e.id: e for e in the_list if e.type == "artist"}

    # Rule 1: album dominates its tracks (>= absorbs a tied track too).
    after_rule1: list[Entity] = []
    for e in the_list:
        if e.type == "track" and e.album_id is not None:
            album = albums_by_id.get(e.album_id)
            if album is not None and album.score >= e.score:
                continue  # dominated by its album
        after_rule1.append(e)

    # Rule 2: artist dominates its tracks/albums (>=).
    final: list[Entity] = []
    for e in after_rule1:
        if e.type in ("track", "album") and e.artist_id is not None:
            artist = artists_by_id.get(e.artist_id)
            if artist is not None and artist.score >= e.score:
                continue  # dominated by its artist
        final.append(e)

    return final
