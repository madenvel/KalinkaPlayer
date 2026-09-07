"""One source's name hits, ranked against the query.

A hit's place is decided by tier first and similarity second. The tiers say
*how* the name answers the query — exactly, once an edition suffix is set
aside, a typo away, as a fragment, through its artist or album, or barely —
and nothing is ever cut: the sources chose what to return, ranking only
decides what leads. Within a tier the kind leads — artists, then albums,
playlists and tracks — so a list reads as runs rather than a shuffle, and
within a kind a whole-string similarity orders the hits, with a penalty for
words the query never asked for, so "The Beatles" leads "The Beatles
1962–1966" even though both contain the query.

Every hit is annotated with its tier and score, which is what lets a client
holding several sources' listings merge them by tier without ranking anything
itself.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Iterable, List, Optional, Sequence

from rapidfuzz import fuzz

from kalinka_plugin_sdk.datamodel import (
    BrowseItem,
    BrowseItemList,
    EmptyList,
    EntityType,
    MatchTier,
    NameMatch,
)
from kalinka_plugin_sdk.inputmodule import InputModule, SearchType

from .config_model import SearchConfig

# Whole-string similarity at which a name is a typo away from the query.
CLOSE_RATIO = 85.0

_TIER_ORDER = {tier: index for index, tier in enumerate(MatchTier)}

# Within a tier the less granular entity leads, so an artist is never sorted
# under its own tracks and each kind reads as one run.
_GRANULARITY = {
    EntityType.ARTIST: 0,
    EntityType.ALBUM: 1,
    EntityType.PLAYLIST: 2,
    EntityType.TRACK: 3,
}

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+")

_EDITION_WORDS = (
    r"remaster\w*|deluxe|edition|expanded|anniversary|bonus|reissue|mono|stereo"
    r"|explicit|clean|single|version"
)
_BRACKETED_EDITION_RE = re.compile(
    rf"\s*[\(\[][^\)\]]*\b(?:{_EDITION_WORDS})\b[^\)\]]*[\)\]]", re.I
)
_DASHED_EDITION_RE = re.compile(
    rf"\s+[-–—]\s+[^-–—]*\b(?:{_EDITION_WORDS})\b[^-–—]*$", re.I
)

_CANDIDATE_TYPES = (
    SearchType.track,
    SearchType.album,
    SearchType.artist,
    SearchType.playlist,
)


def normalize(text: str) -> str:
    """The form two names are compared in: diacritics, case, punctuation and
    spacing set aside."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(_TOKEN_RE.findall(plain.casefold()))


def equivalent_form(text: str) -> str:
    """[normalize] after the corrections a listener would not call a
    difference: a leading article, an edition suffix, ``&`` for ``and``."""
    text = _BRACKETED_EDITION_RE.sub("", text)
    text = _DASHED_EDITION_RE.sub("", text)
    return _ARTICLE_RE.sub("", normalize(text.replace("&", " and ")))


def similarity(query: str, text: str) -> float:
    """Whole-string similarity, 0..100, with a penalty for words and length
    the query did not ask for — the ordering within a tier."""
    q, t = normalize(query), normalize(text)
    if not q or not t:
        return 0.0
    q_tokens, t_tokens = set(q.split()), set(t.split())
    jaccard = len(q_tokens & t_tokens) / len(q_tokens | t_tokens)
    length = min(len(q), len(t)) / max(len(q), len(t))
    return 0.7 * fuzz.ratio(q, t) + 20.0 * jaccard + 10.0 * length


def name_tier(query: str, name: str) -> Optional[MatchTier]:
    """The tier ``name`` alone earns against ``query``; None when it earns
    none of the name tiers."""
    q, n = normalize(query), normalize(name)
    if not q or not n:
        return None
    if q == n:
        return MatchTier.EXACT
    if equivalent_form(query) == equivalent_form(name):
        return MatchTier.EQUIVALENT
    if fuzz.ratio(q, n) >= CLOSE_RATIO:
        return MatchTier.CLOSE
    if q in n or n in q:
        return MatchTier.PARTIAL
    return None


def _context(item: BrowseItem) -> List[str]:
    """The names an item is found through besides its own: an album's
    artist; a track's performer, album artist and album."""
    names: List[Optional[str]] = []
    if item.track is not None:
        track = item.track
        if track.performer is not None:
            names.append(track.performer.name)
        if track.album is not None:
            if track.album.artist is not None:
                names.append(track.album.artist.name)
            names.append(track.album.title)
    elif item.album is not None and item.album.artist is not None:
        names.append(item.album.artist.name)
    return [name for name in names if name]


def match_of(query: str, item: BrowseItem) -> NameMatch:
    """Where ``item`` stands against ``query``: its tier, and the similarity
    of whichever name earned it."""
    tier = name_tier(query, item.name)
    if tier is not None:
        return NameMatch(tier=tier, score=similarity(query, item.name))

    context = _context(item)
    matched = [name for name in context if name_tier(query, name) is not None]
    if matched:
        score = max(similarity(query, name) for name in matched)
        return NameMatch(tier=MatchTier.CONTEXTUAL, score=score)

    score = max(similarity(query, name) for name in [item.name, *context])
    return NameMatch(tier=MatchTier.WEAK, score=score)


def rank(query: str, items: Iterable[BrowseItem]) -> List[BrowseItem]:
    """Every distinct item, annotated with its match and ordered by tier,
    then kind, then score, then the order the sources gave."""
    seen = set()
    ranked = []
    for position, item in enumerate(items):
        key = item.id.to_string
        if key in seen:
            continue
        seen.add(key)
        annotated = item.model_copy(update={"match": match_of(query, item)})
        ranked.append((position, annotated))
    ranked.sort(
        key=lambda entry: (
            _TIER_ORDER[entry[1].match.tier],
            _GRANULARITY.get(entry[1].id.type, len(_GRANULARITY)),
            -entry[1].match.score,
            entry[0],
        )
    )
    return [item for _, item in ranked]


class SourceFailed(Exception):
    """A source could not answer: one of its legs raised. Nothing partial is
    passed off as its listing — the caller reports the source as unavailable."""

    def __init__(self, source: str, cause: BaseException):
        self.source = source
        self.cause = cause
        super().__init__(f"{source}: {cause!r}")


async def collect_name_matches(
    modules: Sequence[InputModule], query: str, cfg: SearchConfig
) -> BrowseItemList:
    """Every hit the given sources return for ``query``, ranked as one list.

    Each source is asked for every entity kind at once. A query that names
    nothing — a mood, a genre, filler — is answered empty without asking:
    there is no name to match, and short titles would score against it by
    coincidence.

    Raises:
        SourceFailed: a source's leg raised; its hits are not silently the
            ones that survived.
    """
    if not query.strip() or not modules or not has_navigational_intent(query):
        return EmptyList(0, 0)

    per_source = await asyncio.gather(
        *(_search_all_kinds(module, query, cfg.candidate_limit) for module in modules)
    )
    ranked = rank(query, (item for items in per_source for item in items))
    return BrowseItemList(offset=0, limit=len(ranked), total=len(ranked), items=ranked)


async def _search_all_kinds(
    module: InputModule, query: str, limit: int
) -> List[BrowseItem]:
    legs = await asyncio.gather(
        *(module.search(kind, query, 0, limit) for kind in _CANDIDATE_TYPES),
        return_exceptions=True,
    )
    items: List[BrowseItem] = []
    for leg in legs:
        if isinstance(leg, BaseException):
            raise SourceFailed(module.module_name(), leg)
        items.extend(leg.items)
    return items


# Descriptor vocabulary (instruments + genres + moods). A query token in here is
# a description, not a name, so it doesn't count as navigational intent (see
# has_navigational_intent): "piano" / "upbeat jazz" describe what to discover
# rather than name a thing to look up. Single tokens, matched per-word.
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
    none, so name matching is skipped: there is no name to match, and scoring
    a long phrase against short titles yields coincidental hits. Skipping it
    also spares the search() fan-out for discovery queries — the common case.
    """
    tokens = set(_TOKEN_RE.findall(query.lower()))
    return bool(tokens - _FILLER_WORDS - _DESCRIPTOR_WORDS)
