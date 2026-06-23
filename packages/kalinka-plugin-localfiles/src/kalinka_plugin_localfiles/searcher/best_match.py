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

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

# Inclusion threshold (rapidfuzz score, 0-100).  These surface as "BEST MATCH"
# at the very top, so err higher rather than lower: a weak match shown as the
# best result is worse than showing nothing.  Start ~70 and tune.
RAPIDFUZZ_CUTOFF: float = 70.0

# Maximum number of entities in the BEST MATCH block.
MAX_RESULTS: int = 6

# Scorer applied to (query, entity.name).  WRatio is robust to word-order and
# partial matches ("piano guys" vs "The Piano Guys").  Swap for
# fuzz.token_set_ratio / fuzz.partial_ratio if a different behaviour is wanted.
SCORER = fuzz.WRatio


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Entity:
    """A library entity (artist / album / track) considered for BEST MATCH.

    `score` is populated by assemble_best_match; callers may leave it at the
    default.  Redundancy checks operate on IDs (album_id / artist_id), never on
    name strings.
    """

    id: str
    type: str  # "artist" | "album" | "track"
    name: str
    score: float = 0.0
    album_id: Optional[str] = None  # tracks only
    artist_id: Optional[str] = None  # tracks and albums


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def assemble_best_match(candidates: list[Entity], query: str) -> list[Entity]:
    """Score, cut off, sort, truncate to MAX_RESULTS, then remove album/artist-
    dominated redundancies.

    Returns 0..MAX_RESULTS entities, score-descending, exactly as produced (no
    re-sort or regroup by type).  An empty list means the UI renders no BEST
    MATCH section.
    """
    # Step 1 — score every candidate; discard below the cutoff.
    survivors: list[Entity] = []
    for entity in candidates:
        entity.score = SCORER(query, entity.name)
        if entity.score >= RAPIDFUZZ_CUTOFF:
            survivors.append(entity)

    # Step 2 — sort by score descending, keep the top MAX_RESULTS.  This is
    # "the list" the redundancy rules operate on.
    survivors.sort(key=lambda e: e.score, reverse=True)
    the_list = survivors[:MAX_RESULTS]

    # Step 3 — remove redundancy.  Index by id+type so lookups are exact and a
    # track and its same-named album/artist never collide.
    albums_by_id = {e.id: e for e in the_list if e.type == "album"}
    artists_by_id = {e.id: e for e in the_list if e.type == "artist"}

    # Rule 1: album dominates its tracks (strictly greater).
    after_rule1: list[Entity] = []
    for e in the_list:
        if e.type == "track" and e.album_id is not None:
            album = albums_by_id.get(e.album_id)
            if album is not None and album.score > e.score:
                continue  # dominated by its album
        after_rule1.append(e)

    # Rule 2: artist dominates its tracks/albums (strictly greater).
    final: list[Entity] = []
    for e in after_rule1:
        if e.type in ("track", "album") and e.artist_id is not None:
            artist = artists_by_id.get(e.artist_id)
            if artist is not None and artist.score > e.score:
                continue  # dominated by its artist
        final.append(e)

    # Step 4 — already in score-descending order; return as-is.
    return final
