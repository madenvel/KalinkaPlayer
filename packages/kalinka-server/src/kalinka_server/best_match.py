""""BEST MATCH" assembly for cross-source search.

Takes a flat list of scored entities (artists, albums, tracks, playlists)
recalled from every input module's ``search()`` and produces a small,
high-confidence ordered set answering *navigational* intent ("I know what this
is called, take me to it"). The result renders as a single flat "BEST MATCH"
block at the very top of the AI-search results, above the per-source semantic
suggestions.

This is the literal/FTS side; the semantic suggestions come from each module's
``ai_search()`` and are assembled separately. The two legs are never deduped
against each other — overlap is expected.

Algorithm (executed in this exact order — see :func:`assemble_best_match`):
    1. Score every candidate against the query; discard score < cutoff.
    2. Sort surviving entities (all types mixed) by score descending; keep the
       top ``max_results``.
    3. Remove redundancy on the truncated list (strictly-by-id):
         a. album dominates its tracks (album.score >= track.score)
         b. artist dominates its tracks/albums (artist.score >= entity.score)
    4. Return the remaining entities, still score-descending.

The truncate-before-dedup ordering (step 2 before step 3) is deliberate: a
strong-but-redundant entity can crowd out a weaker survivor, leaving fewer than
``max_results`` items. That is intended; we do not backfill.

This module was lifted from the localfiles searcher when BEST MATCH became a
server-side, multi-source concern. The scoring algorithm is unchanged; the only
addition is :func:`browse_item_to_entity`, which adapts the ``BrowseItem``s the
input modules already return into the ``Entity`` the algorithm scores.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz

from kalinka_plugin_sdk.datamodel import BrowseItem, EntityType

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

# Inclusion threshold (rapidfuzz 0-100); a weak top "BEST MATCH" is worse than
# none. 88: the case-folded scorer lands real matches >=90 but a query sharing
# one word with a title ~85.
RAPIDFUZZ_CUTOFF: float = 88.0

# Maximum number of entities in the BEST MATCH block.
MAX_RESULTS: int = 6

# Scorer applied to (query, entity.name). WRatio is robust to word-order and
# partial matches ("piano guys" vs "The Piano Guys").
SCORER = fuzz.WRatio


# ---------------------------------------------------------------------------
# Query classification (navigational vs descriptive)
# ---------------------------------------------------------------------------

# Descriptor vocabulary (instruments + genres + moods). A query token in here is
# a description, not a name — so even when it matches an entity name ("piano" ->
# "The Piano Guys", "jazz" -> Queen's "Jazz") it's a discovery query and the AI
# suggestions are kept. Single tokens only; matched per-word against the query.
_DESCRIPTOR_WORDS = frozenset({
    # instruments
    "piano", "guitar", "guitars", "violin", "cello", "drums", "drum", "bass",
    "percussion", "saxophone", "sax", "synthesizer", "synth", "synths", "flute",
    "trumpet", "organ", "harp", "harmonica", "accordion", "banjo", "ukulele",
    "clarinet", "vocals", "vocal", "choir", "strings", "brass", "keyboard",
    "acoustic", "instrumental", "orchestra",
    # genres
    "rock", "jazz", "electronic", "electronica", "ambient", "classical", "rap",
    "hop", "pop", "metal", "folk", "blues", "techno", "house", "funk", "soul",
    "reggae", "country", "punk", "disco", "edm", "dubstep", "trance", "indie",
    "gospel", "latin", "orchestral", "soundtrack", "lofi", "grunge", "opera",
    "synthwave", "ska", "swing", "bluegrass",
    # moods (mirrors the mood vocabulary)
    "happy", "upbeat", "energetic", "joyful", "euphoric", "triumphant", "epic",
    "playful", "exciting", "uplifting", "calm", "peaceful", "serene", "chill",
    "relaxed", "relaxing", "soothing", "mellow", "dreamy", "romantic", "tender",
    "warm", "hopeful", "ethereal", "aggressive", "angry", "tense", "anxious",
    "frantic", "menacing", "dark", "eerie", "chaotic", "intense", "sad",
    "melancholic", "somber", "gloomy", "depressing", "mournful", "lonely",
    "bleak", "nostalgic", "wistful", "bittersweet", "mysterious",
})


def is_descriptive(query: str) -> bool:
    """True if any query word is a mood/genre/instrument descriptor — i.e. a
    discovery query, not a name lookup. Used to decide whether a strong BEST
    MATCH should suppress the semantic AI suggestions (a name lookup) or leave
    them (a descriptor like "piano" / "jazz" that happens to match a name)."""
    return bool(set(re.findall(r"[a-z]+", query.lower())) & _DESCRIPTOR_WORDS)


# Filler / stop words carried by natural-language queries ("play me something
# for tonight"). With the descriptors above, these are the words that should
# NOT count as a name to look up.
_FILLER_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "by", "with", "from", "into", "my", "me", "i", "you", "your", "we", "us",
    "it", "its", "this", "that", "these", "those", "some", "something",
    "anything", "like", "want", "need", "give", "play", "playing", "song",
    "songs", "music", "track", "tracks", "tune", "tunes", "sound", "sounds",
    "playlist", "vibe", "vibes", "mood", "feeling", "feel", "get", "got", "im",
    "am", "are", "is", "be", "now", "tonight", "today", "day", "night", "time",
    "really", "very", "more", "bit", "little", "kinda", "sorta", "stuff",
})


def has_navigational_intent(query: str) -> bool:
    """True if the query has at least one token that is neither a filler nor a
    descriptor word — i.e. plausibly the name of a thing to look up.

    A pure mood/genre/filler phrase ("something melancholic for tonight") has
    none, so BEST MATCH is skipped: there is no name to match, and scoring a
    long NL phrase against short titles yields coincidental hits (a track
    literally titled "Something" partial-matches at ~90, above the cutoff).
    Skipping it also avoids the search() fan-out for discovery queries — the
    common ai_search case — so only the ai_search() legs run.
    """
    tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
    return bool(tokens - _FILLER_WORDS - _DESCRIPTOR_WORDS)


# ---------------------------------------------------------------------------
# Name folding
# ---------------------------------------------------------------------------


def fold_diacritics(name: str) -> str:
    """Strip diacritics while preserving case, spacing, and punctuation, so an
    unaccented query matches an accented name ("Noi Kabat" -> "Női Kabát")."""
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name)
    return "".join(c for c in n if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Entity:
    """A scored candidate. ``id`` / ``album_id`` / ``artist_id`` are the full
    ``EntityId`` strings (``kalinka:source:type:id``) so dominance rules match
    by-id and never collapse entities from different sources. ``score`` is
    filled in by :func:`assemble_best_match`."""

    id: str
    type: str  # "artist" | "album" | "track" | "playlist"
    name: str
    score: float = 0.0
    album_id: Optional[str] = None  # tracks only
    artist_id: Optional[str] = None  # tracks and albums


def browse_item_to_entity(item: BrowseItem) -> Entity:
    """Adapt a ``BrowseItem`` from a module's ``search()`` into a scoring
    ``Entity``.

    The entity type comes from the id (authoritative); album/artist relations
    are read from the nested metadata when present so the dominance rules can
    fire. Missing relations leave the corresponding field None, which simply
    means that entity can't be dominated — never an error.
    """
    etype = item.id.type
    album_id: Optional[str] = None
    artist_id: Optional[str] = None

    if etype == EntityType.TRACK and item.track is not None:
        if item.track.album is not None:
            album_id = item.track.album.id.to_string
            if item.track.album.artist is not None:
                artist_id = item.track.album.artist.id.to_string
        if artist_id is None and item.track.performer is not None:
            artist_id = item.track.performer.id.to_string
    elif etype == EntityType.ALBUM and item.album is not None:
        if item.album.artist is not None:
            artist_id = item.album.artist.id.to_string

    return Entity(
        id=item.id.to_string,
        type=etype.value,
        name=item.name,
        album_id=album_id,
        artist_id=artist_id,
    )


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
    (no re-sort or regroup by type). An empty list means no BEST MATCH section.
    Playlists are scored and truncated like any entity but participate in no
    dominance rule, so a matching playlist always survives on its own merit.
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

    # Remove redundancy. Index by id so a track and its same-named album/artist
    # never collide.
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
