"""
Rule-based natural-language query parser for Kalinka local music search.

Extracts structured constraints (genre, mood, danceability, "songs like this")
from free-text queries, leaving the remainder as a plain-text FTS query.
No ML models — just keyword matching against known vocabularies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Shared vocabularies (canonical source — imported by localfiles.py too)
# ---------------------------------------------------------------------------

# Representative keywords for mood clusters (index = mood_mirex cluster id 0-4)
MOOD_CLUSTER_KEYWORDS: list[list[str]] = [
    ["happy", "joyful", "upbeat", "energetic", "fun"],  # 0
    ["angry", "aggressive", "intense", "heavy", "powerful"],  # 1
    ["calm", "peaceful", "relaxing", "ambient", "soft"],  # 2
    ["melancholic", "sad", "emotional", "dark", "nostalgic"],  # 3
    ["romantic", "tender", "sentimental", "warm"],  # 4
]

# Genre vocabulary — keywords that match against Discogs400 labels stored as
# "topgenre---subgenre" (lowercased).  Include top-level genres, common
# sub-genres, and popular aliases so user queries like "jazz" or "techno"
# match the stored labels.  The query parser strips matched keywords from
# the FTS remainder, so keep this list curated (no overly-generic words).
GENRE_KEYWORDS: list[str] = [
    # Top-level Discogs categories
    "blues",
    "classical",
    "electronic",
    "folk",
    "funk",
    "hip hop",
    "hip-hop",
    "jazz",
    "latin",
    "pop",
    "reggae",
    "rock",
    "soul",
    # Popular sub-genres (must appear in the Discogs400 label set)
    "acid house",
    "acid jazz",
    "alternative rock",
    "ambient",
    "bluegrass",
    "bossa nova",
    "breakbeat",
    "brit pop",
    "classic rock",
    "cool jazz",
    "country",
    "country rock",
    "dancehall",
    "death metal",
    "deep house",
    "disco",
    "doom metal",
    "downtempo",
    "dream pop",
    "drum n bass",
    "dub",
    "dubstep",
    "electro",
    "emo",
    "flamenco",
    "folk rock",
    "free jazz",
    "funk metal",
    "fusion",
    "garage rock",
    "gospel",
    "goth rock",
    "grunge",
    "hard rock",
    "hardcore",
    "heavy metal",
    "house",
    "idm",
    "indie pop",
    "indie rock",
    "industrial",
    "metal",
    "metalcore",
    "minimal",
    "new age",
    "new wave",
    "nu metal",
    "opera",
    "pop rock",
    "post-punk",
    "post rock",
    "prog rock",
    "progressive metal",
    "psychedelic rock",
    "punk",
    "r&b",
    "rnb",
    "salsa",
    "samba",
    "shoegaze",
    "ska",
    "smooth jazz",
    "soft rock",
    "soul-jazz",
    "soundtrack",
    "synth-pop",
    "synthwave",
    "tech house",
    "techno",
    "trance",
    "trap",
    "trip hop",
    # Aliases (not in Discogs labels but map to common sub-genres)
    "acoustic",
    "americana",
    "dance",
    "edm",
    "indie",
    "instrumental",
    "meditation",
    "rap",
]

# Danceability hint phrases → (min, max) ranges on a 0–1 scale
_DANCE_HINTS: list[tuple[list[str], float | None, float | None]] = [
    (
        ["dance", "danceable", "dance floor", "dance-floor", "groovy", "bouncy"],
        0.6,
        None,
    ),
    (["chill", "laid back", "laid-back", "mellow", "downtempo"], None, 0.4),
]

# Patterns that signal a "songs like this" intent
_SIMILAR_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bsongs?\s+like\s+this\b", re.IGNORECASE),
    re.compile(r"\bsimilar\s+to\s+this\b", re.IGNORECASE),
    re.compile(r"\bmore\s+like\s+this\b", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# ParsedQuery
# ---------------------------------------------------------------------------


@dataclass
class ParsedQuery:
    """Structured representation of a user's search intent."""

    raw: str
    text_query: str = ""
    genres: list[str] = field(default_factory=list)
    mood_clusters: list[int] = field(default_factory=list)
    min_danceability: Optional[float] = None
    max_danceability: Optional[float] = None
    similar_to_track_id: Optional[str] = None

    @property
    def has_tag_constraints(self) -> bool:
        return bool(
            self.genres
            or self.mood_clusters
            or self.min_danceability is not None
            or self.max_danceability is not None
        )

    @property
    def is_similar_query(self) -> bool:
        return self.similar_to_track_id is not None


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_query(query: str, *, current_track_id: Optional[str] = None) -> ParsedQuery:
    """
    Parse a natural-language music query into structured constraints.

    Parameters
    ----------
    query : str
        Free-text query from the user.
    current_track_id : str | None
        If the player has a currently-playing track, pass its ID here so that
        "songs like this" queries can reference it.

    Returns
    -------
    ParsedQuery
        Structured representation with extracted constraints and the
        remaining text suitable for FTS5 MATCH.
    """
    q = query.strip()
    q_lower = q.lower()
    remainder = q_lower

    # --- "songs like this" detection ---
    similar_to: Optional[str] = None
    for pat in _SIMILAR_PATTERNS:
        if pat.search(q_lower):
            similar_to = current_track_id
            remainder = pat.sub("", remainder)
            break

    # --- Genre extraction ---
    # Sort by length descending so "hip hop" matches before "pop"
    genres: list[str] = []
    for g in sorted(GENRE_KEYWORDS, key=len, reverse=True):
        # Use word-boundary-like matching for short keywords to avoid
        # false positives (e.g. "pop" inside "popular")
        if len(g) <= 4:
            pattern = r"(?<![a-z])" + re.escape(g) + r"(?![a-z])"
            if re.search(pattern, remainder):
                genres.append(g)
                remainder = re.sub(pattern, " ", remainder, count=1)
        else:
            if g in remainder:
                genres.append(g)
                remainder = remainder.replace(g, " ", 1)

    # --- Mood extraction ---
    mood_clusters: list[int] = []
    for cluster_id, keywords in enumerate(MOOD_CLUSTER_KEYWORDS):
        for kw in keywords:
            if len(kw) <= 4:
                pattern = r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])"
                if re.search(pattern, remainder):
                    if cluster_id not in mood_clusters:
                        mood_clusters.append(cluster_id)
                    remainder = re.sub(pattern, " ", remainder, count=1)
            else:
                if kw in remainder:
                    if cluster_id not in mood_clusters:
                        mood_clusters.append(cluster_id)
                    remainder = remainder.replace(kw, " ", 1)

    # --- Danceability hints ---
    min_dance: Optional[float] = None
    max_dance: Optional[float] = None
    for keywords, lo, hi in _DANCE_HINTS:
        for kw in keywords:
            if kw in remainder:
                if lo is not None:
                    min_dance = max(min_dance or 0.0, lo)
                if hi is not None:
                    max_dance = min(max_dance or 1.0, hi)
                remainder = remainder.replace(kw, " ", 1)

    # --- Clean up remainder for FTS ---
    # Remove filler words that don't help FTS
    remainder = re.sub(
        r"\b(songs?|tracks?|music|by|with|for|the|a|an|and|or|of|in|on|to|my|me|some|find|play|show|give)\b",
        " ",
        remainder,
    )
    # Collapse whitespace
    text_query = " ".join(remainder.split()).strip()

    return ParsedQuery(
        raw=query,
        text_query=text_query,
        genres=genres,
        mood_clusters=mood_clusters,
        min_danceability=min_dance,
        max_danceability=max_dance,
        similar_to_track_id=similar_to,
    )


# ---------------------------------------------------------------------------
# Tag overlap scoring (used by both searcher ranking and localfiles re-ranking)
# ---------------------------------------------------------------------------


def extract_query_tags(query: str) -> dict:
    """
    Extract genre and mood hints from a natural-language query by keyword matching.
    Returns ``{"genres": [str,...], "mood_clusters": [int,...]}``.
    """
    parsed = parse_query(query)
    return {"genres": parsed.genres, "mood_clusters": parsed.mood_clusters}


def tag_overlap_score(query_tags: dict, track_tags: dict) -> float:
    """
    Score 0–1 based on overlap between extracted query tags and a track's
    ``tags_predicted`` JSON.  Higher is more relevant.
    """
    if not track_tags:
        return 0.0

    score = 0.0
    total = 0

    q_genres = query_tags.get("genres", [])
    t_genres = track_tags.get("genres") or []
    if q_genres:
        total += 1
        genre_labels = " ".join(g.get("label", "") for g in t_genres).lower()
        if any(qg in genre_labels for qg in q_genres):
            score += 1.0

    q_moods = query_tags.get("mood_clusters", [])
    t_mood = track_tags.get("mood_cluster")
    if q_moods and t_mood is not None:
        total += 1
        if t_mood in q_moods:
            score += 1.0

    return score / total if total > 0 else 0.0
