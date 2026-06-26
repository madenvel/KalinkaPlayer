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
    scores aren't comparable). Only the navigational cut-off gates them.

The legs run in parallel but are not blended. A strong navigational match for a
non-descriptor query suppresses the semantic suggestions (a name lookup wants
the named thing, not "songs that sound like the words").

Related Albums / Related Artists (derived from the suggestion tracks) are a
planned addition below the suggestion cards; not implemented yet.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

from kalinka_plugin_sdk.datamodel import (
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
    MAX_RESULTS,
    Entity,
    assemble_best_match,
    browse_item_to_entity,
    has_navigational_intent,
)

logger = logging.getLogger(__name__.split(".")[-1])

# Candidates pulled per type per source to feed BEST MATCH. The rapidfuzz
# cutoff does the real filtering; this only bounds recall/cost.
CANDIDATE_LIMIT = 50
# Semantic suggestion tracks requested per source for its AI SUGGESTIONS card.
AI_SUGGESTIONS_LIMIT = 20

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
    modules: List[InputModule], query: str, offset: int, limit: int
) -> BrowseItemList:
    """Build the merged BEST MATCH + per-source AI SUGGESTIONS section list."""
    if not query.strip() or not modules:
        return EmptyList(offset, limit)

    # BEST MATCH (and its search() fan-out) only makes sense for a query that
    # names something. A pure mood/genre phrase skips it — no junk literal hits,
    # and the Jamendo search() round-trips are avoided for the common case.
    navigational = has_navigational_intent(query)

    per_source = await asyncio.gather(
        *(_search_one_source(module, query, navigational) for module in modules),
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

    winners = _merge_best_match(candidates_by_source, query)
    best_match_section = _best_match_section(
        [items_by_id[w.id] for w in winners if w.id in items_by_id]
    )

    # BEST MATCH on top (when the query named something we found), then every
    # source's AI suggestions. The suggestions are always shown — a query that
    # reads like a name but isn't in our word lists ("workout music") must not
    # silently lose them.
    bm = [best_match_section] if best_match_section is not None else []
    sections = bm + ai_sections

    return BrowseItemList(
        offset=offset,
        limit=limit,
        total=len(sections),
        items=sections[offset : offset + limit],
    )


async def _search_one_source(
    module: InputModule, query: str, navigational: bool
) -> _SourceResults:
    """Run a source's ``ai_search()`` and — only for a navigational query — its
    four ``search()`` types, in parallel. A failing leg is logged and skipped:
    one bad source must not sink the whole query."""
    name = module.module_name()
    search_coros = (
        [module.search(t, query, 0, CANDIDATE_LIMIT) for t in _CANDIDATE_TYPES]
        if navigational
        else []
    )
    legs = await asyncio.gather(
        *search_coros,
        module.ai_search(query, 0, AI_SUGGESTIONS_LIMIT),
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
    candidates_by_source: Dict[str, List[Entity]], query: str
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
        assemble_best_match(cands, query) for cands in candidates_by_source.values()
    ]
    merged: List[Entity] = []
    for tier in itertools.zip_longest(*ranked_per_source):
        for entity in tier:
            if entity is None:
                continue
            merged.append(entity)
            if len(merged) >= MAX_RESULTS:
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
