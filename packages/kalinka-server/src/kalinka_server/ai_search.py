"""Cross-source AI-search assembly.

The ``/ai_search`` endpoint runs two independent legs over every input module
and assembles the presentation here (the modules return raw data; the server
owns the catalog/preview layout so the UI renders sections verbatim):

  * **BEST MATCH** — a single merged, literal/navigational block built from the
    modules' ``search()`` results (tracks / albums / artists / playlists),
    scored and de-duplicated by :func:`best_match.assemble_best_match`.
  * **AI SUGGESTIONS** — each module's ``ai_search()`` returns its own
    presentation-ready section(s) (typically a CARD catalog of semantically
    ranked tracks). The server appends these verbatim after BEST MATCH, one
    source's after another — never merged across sources (their relevance
    scores aren't comparable).

The legs run in parallel but are not blended. The suggestions are shown except
for one case: when the query is a near-exact whole-string match against a BEST
MATCH name (a pure name lookup like "jean michel jarre" → "Jean-Michel Jarre"),
they're hidden — the user wants the named thing, not songs that sound like the
words. A partial / extra-word query ("workout music") keeps them.

  * **Related Albums / Related Artists** — derived from the suggestion tracks
    (the union across sources), ranked by how many suggestions point at each
    album / artist, and appended below the suggestion cards.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

from kalinka_plugin_sdk.datamodel import (
    Album,
    Artist,
    BrowseItem,
    BrowseItemList,
    Catalog,
    EmptyList,
    EntityId,
    EntityType,
    Preview,
    PreviewContentType,
    PreviewType,
)
from kalinka_plugin_sdk.inputmodule import InputModule, SearchType

from .best_match import (
    Entity,
    assemble_best_match,
    browse_item_to_entity,
    full_match_score,
    has_navigational_intent,
)
from .config_model import SearchConfig

logger = logging.getLogger(__name__.split(".")[-1])

# Entity types pulled from search() as BEST MATCH candidates.
_CANDIDATE_TYPES = (
    SearchType.track,
    SearchType.album,
    SearchType.artist,
    SearchType.playlist,
)


@dataclass
class _SourceResults:
    """One source's contribution: literal candidates (for the merged BEST
    MATCH) and its own ready-to-append ai_search() section(s)."""

    candidates: List[BrowseItem]
    ai_sections: List[BrowseItem]


async def assemble_ai_search(
    modules: List[InputModule],
    query: str,
    offset: int,
    limit: int,
    cfg: Optional[SearchConfig] = None,
) -> BrowseItemList:
    """Build the merged BEST MATCH + per-source AI SUGGESTIONS section list.

    ``cfg`` carries the tunables (score cut-offs, limits); defaults are used when
    it is omitted (e.g. in tests).
    """
    cfg = cfg or SearchConfig()
    if not query.strip() or not modules:
        return EmptyList(offset, limit)

    # BEST MATCH (and its search() fan-out) only makes sense for a query that
    # names something. A pure mood/genre phrase skips it — no junk literal hits,
    # and the Jamendo search() round-trips are avoided for the common case.
    navigational = has_navigational_intent(query)

    per_source = await asyncio.gather(
        *(_search_one_source(module, query, navigational, cfg) for module in modules),
        return_exceptions=True,
    )

    # Collect literal candidates grouped by source (for a fair merged BEST
    # MATCH) and each source's ready-to-append ai_search() card(s). items_by_id
    # maps a scored entity back to its rich BrowseItem.
    candidates_by_source: Dict[str, List[Entity]] = defaultdict(list)
    items_by_id: dict[str, BrowseItem] = {}
    ai_sections: List[BrowseItem] = []
    for src in per_source:
        if isinstance(src, BaseException):
            # A whole source blew up outside its own leg handling — skip it so
            # one bad source can't sink the query.
            logger.warning("ai_search: source failed: %s", src)
            continue
        for item in src.candidates:
            items_by_id[item.id.to_string] = item
            candidates_by_source[item.id.source].append(browse_item_to_entity(item))
        ai_sections.extend(src.ai_sections)

    winners = _merge_best_match(candidates_by_source, query, cfg)
    best_match_section = _best_match_section(
        [items_by_id[w.id] for w in winners if w.id in items_by_id]
    )

    # Hide the AI suggestions only when the query *is* essentially a name we
    # found — a near-exact whole-string match against a BEST MATCH entity
    # ("jean michel jarre" -> "Jean-Michel Jarre"). A partial / extra-word match
    # ("workout music" -> "Workout") keeps them: full_match_score penalises
    # leftover words on either side, so only a true name lookup clears the bar.
    # Result-based, not a brittle query-word list.
    if best_match_section is not None and any(
        full_match_score(query, w.name) >= cfg.ai_suppress_full_match_score
        for w in winners
    ):
        logger.info("ai_search: full-name match for %r — AI suggestions hidden", query)
        ai_sections = []

    # BEST MATCH on top (when the query named something we found), then every
    # source's AI suggestions, then the Related Albums / Artists derived from
    # those suggestions (empty when the suggestions were hidden).
    bm = [best_match_section] if best_match_section is not None else []
    sections = bm + ai_sections + _related_sections(ai_sections, cfg)

    return BrowseItemList(
        offset=offset,
        limit=limit,
        total=len(sections),
        items=sections[offset : offset + limit],
    )


async def _search_one_source(
    module: InputModule, query: str, navigational: bool, cfg: SearchConfig
) -> _SourceResults:
    """Run a source's ``ai_search()`` and — only for a navigational query — its
    four ``search()`` types, in parallel. A failing leg is logged and skipped:
    one bad source must not sink the whole query."""
    name = module.module_name()
    search_coros = (
        [module.search(t, query, 0, cfg.candidate_limit) for t in _CANDIDATE_TYPES]
        if navigational
        else []
    )
    legs = await asyncio.gather(
        *search_coros,
        module.ai_search(query, 0, cfg.ai_suggestions_limit),
        return_exceptions=True,
    )
    *search_legs, ai_leg = legs

    candidates: List[BrowseItem] = []
    for stype, leg in zip(_CANDIDATE_TYPES, search_legs):
        if isinstance(leg, BrowseItemList):
            candidates.extend(leg.items)
        elif isinstance(leg, BaseException):
            logger.warning("search(%s) failed for %s: %s", stype.value, name, leg)

    if isinstance(ai_leg, BrowseItemList):
        ai_sections = ai_leg.items
    else:
        if isinstance(ai_leg, BaseException):
            logger.warning("ai_search failed for %s: %s", name, ai_leg)
        ai_sections = []

    return _SourceResults(candidates=candidates, ai_sections=ai_sections)


def _merge_best_match(
    candidates_by_source: Dict[str, List[Entity]], query: str, cfg: SearchConfig
) -> List[Entity]:
    """Assemble BEST MATCH per source, then interleave round-robin by rank.

    A single global "score, then truncate to N" merge lets a large public
    catalog (many coincidental name matches) crowd a smaller source's genuine
    match out of the top N — e.g. a dozen Jamendo playlists named "jarre" evict
    the user's own "Jean-Michel Jarre". Scoring / cut-off / dedup still run per
    source (ids are source-scoped, so dominance never crossed sources anyway);
    interleaving each source's ranked winners guarantees every source that
    matched is represented before the N-slot cap is reached. Source order
    follows module order. With one source this is identical to a plain
    assemble_best_match.
    """
    ranked_per_source = [
        assemble_best_match(
            cands,
            query,
            cutoff=cfg.best_match_min_score,
            max_results=cfg.best_match_max_results,
        )
        for cands in candidates_by_source.values()
    ]
    merged: List[Entity] = []
    for tier in itertools.zip_longest(*ranked_per_source):
        for entity in tier:
            if entity is None:
                continue
            merged.append(entity)
            if len(merged) >= cfg.best_match_max_results:
                return merged
    return merged


def _best_match_section(items: List[BrowseItem]) -> Optional[BrowseItem]:
    """Wrap the ordered BEST MATCH winners (mixed entity types) in a single flat
    TILE section. None when empty so the UI renders no header."""
    if not items:
        return None
    cat = EntityId(id="best_match", type=EntityType.CATALOG, source="server")
    return BrowseItem(
        id=cat,
        name="BEST MATCH",
        subname="Top results for your search",
        can_browse=False,
        can_add=False,
        catalog=Catalog(
            id=cat,
            title="BEST MATCH",
            preview_config=Preview(
                type=PreviewType.TILE,
                content_type=PreviewContentType.CATALOG,
                icon="best_match",
                items_count=len(items),
            ),
        ),
        sections=items,
    )


def _related_sections(
    ai_sections: List[BrowseItem], cfg: SearchConfig
) -> List[BrowseItem]:
    """Derive Related Albums / Related Artists from the AI suggestion tracks.

    Rolls up the tracks inside every source's card (their union, in rank order)
    by album and by artist, ranks each by suggestion count then first
    appearance, and wraps the top ``related_max_results`` of each into a TILE
    section. Empty in, empty out — so when the suggestions were hidden, no
    Related rows appear.
    """
    album_pairs: list = []
    artist_pairs: list = []
    for card in ai_sections:
        for item in card.sections or []:
            track = item.track
            if track is None:
                continue
            if track.album is not None:
                album_pairs.append((track.album.id.to_string, track.album))
            # Prefer the track's own performer over the album artist, which can
            # be "Various Artists" on a compilation.
            artist = track.performer or (track.album.artist if track.album else None)
            if artist is not None:
                artist_pairs.append((artist.id.to_string, artist))

    sections: List[BrowseItem] = []
    album_cards = [_album_card(a) for a in _rollup(album_pairs, cfg.related_max_results)]
    if album_cards:
        sections.append(
            _related_catalog("Related Albums", "album", PreviewContentType.ALBUM, album_cards)
        )
    artist_cards = [_artist_card(a) for a in _rollup(artist_pairs, cfg.related_max_results)]
    if artist_cards:
        sections.append(
            _related_catalog(
                "Related Artists", "artist", PreviewContentType.ARTIST, artist_cards
            )
        )
    return sections


def _rollup(pairs: list, limit: int) -> list:
    """Dedup (id, entity) pairs given in rank order, rank by occurrence count
    then first appearance, and return the top ``limit`` entities. ``first``'s
    insertion order is the first-appearance order, and the sort is stable, so
    the count sort keeps that as the tie-break for free."""
    counts = Counter(key for key, _ in pairs)
    first: dict = {}
    for key, item in pairs:
        first.setdefault(key, item)
    ranked = sorted(first.items(), key=lambda kv: -counts[kv[0]])
    return [item for _, item in ranked[:limit]]


def _album_card(album: Album) -> BrowseItem:
    return BrowseItem(
        id=album.id,
        name=album.title,
        subname=album.artist.name if album.artist else None,
        can_browse=True,
        can_add=True,
        album=album,
    )


def _artist_card(artist: Artist) -> BrowseItem:
    return BrowseItem(id=artist.id, name=artist.name, can_browse=True, artist=artist)


def _related_catalog(
    title: str, icon: str, content_type: PreviewContentType, items: List[BrowseItem]
) -> BrowseItem:
    cat = EntityId(id=f"ai_search:related:{icon}", type=EntityType.CATALOG, source="server")
    return BrowseItem(
        id=cat,
        name=title,
        can_browse=False,
        can_add=False,
        catalog=Catalog(
            id=cat,
            title=title,
            preview_config=Preview(
                type=PreviewType.TILE,
                content_type=content_type,
                icon=icon,
                items_count=len(items),
            ),
        ),
        sections=items,
    )
