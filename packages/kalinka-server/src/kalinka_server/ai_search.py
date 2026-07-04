"""Cross-source AI-search assembly.

The ``/ai_search`` endpoint runs independent legs over every input module
and assembles the presentation here (the modules return raw data; the server
owns the catalog/preview layout so the UI renders sections verbatim):

  * **CATALOG ROUTES** — when the query names a browse shelf ("recently added
    to the library"), the matching root cards are prepended (see
    :mod:`query_router`). Additive: the other legs still run, so a routing
    false-positive costs one extra card, not the results. Dropped when the
    query turns out to be a name lookup — "New Order" is a band, not the
    "New Releases" shelf.
  * **BEST MATCH** — one literal/navigational section *per source*, built from
    that source's ``search()`` results (tracks / albums / artists / playlists),
    scored and de-duplicated by :func:`best_match.assemble_best_match` and
    titled with the source's display name.
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

  * **Related Artists** — derived from the suggestion tracks (the union across
    sources), ranked by how many suggestions point at each artist, and appended
    below the suggestion cards.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional

from kalinka_plugin_sdk.datamodel import (
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

from typing import TYPE_CHECKING

from .best_match import (
    assemble_best_match,
    browse_item_to_entity,
    full_match_score,
    has_navigational_intent,
)
from .config_model import SearchConfig

if TYPE_CHECKING:
    from .query_router import CatalogRouter

logger = logging.getLogger(__name__.split(".")[-1])

# Entity types pulled from search() as BEST MATCH candidates.
_CANDIDATE_TYPES = (
    SearchType.track,
    SearchType.album,
    SearchType.artist,
    SearchType.playlist,
)

# Section ordering by source: the user's own library first, then every other
# source alphabetically by name.
def _source_rank(name: str) -> tuple:
    return (0 if name == "localfiles" else 1, name)


@dataclass
class _SourceResults:
    """One source's contribution: its display name, its literal candidates (for
    that source's own BEST MATCH section) and its ready-to-append ai_search()
    section(s)."""

    source: str
    display_name: str
    candidates: List[BrowseItem]
    ai_sections: List[BrowseItem]


async def assemble_ai_search(
    modules: List[InputModule],
    query: str,
    offset: int,
    limit: int,
    cfg: Optional[SearchConfig] = None,
    router: "Optional[CatalogRouter]" = None,
) -> BrowseItemList:
    """Build the section list: routed catalog shortcuts, then a per-source
    BEST MATCH section, then a per-source AI SUGGESTIONS card, then the
    derived Related Artists.

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

    # Catalog routing overlaps with the per-source fan-out; route() never
    # raises. Restricted to the sources this request targets.
    route_task = (
        asyncio.create_task(
            router.route(query, {m.module_name() for m in modules}, cfg)
        )
        if router is not None
        else None
    )

    per_source = await asyncio.gather(
        *(_search_one_source(module, query, navigational, cfg) for module in modules),
        return_exceptions=True,
    )

    # Rank sources so the user's own library leads, then every other source
    # alphabetically. Applied before splitting into rows so both the BEST MATCH
    # and the AI suggestion sections put localfiles first.
    paired = sorted(
        zip(modules, per_source), key=lambda mp: _source_rank(mp[0].module_name())
    )

    # Each source gets its own BEST MATCH section (no cross-source merge), and
    # its own AI card — hidden when *that source's* best match is a full-name
    # lookup. Best-match sections lead, then the suggestion cards.
    bm_sections: List[BrowseItem] = []
    ai_cards: List[BrowseItem] = []
    any_name_lookup = False
    # Maps EntityId.source -> module for the related-artist lookups. Keyed by
    # the source string each module emits on its own cards, NOT module_name()
    # — the two differ (e.g. "Jamendo" vs "jamendo").
    by_source: dict[str, InputModule] = {}
    for module, src in paired:
        if isinstance(src, BaseException):
            # A whole source blew up outside its own leg handling — skip it so
            # one bad source can't sink the query.
            logger.warning("ai_search: source %s failed: %s", module.module_name(), src)
            continue
        for card in src.ai_sections:
            by_source.setdefault(card.id.source, module)
        section, is_name_lookup = _source_best_match(src, query, cfg)
        if section is not None:
            bm_sections.append(section)
        if is_name_lookup:
            any_name_lookup = True
            logger.info(
                "ai_search: full-name match for %r in %s — its AI hidden",
                query, src.source,
            )
        else:
            ai_cards.extend(src.ai_sections)

    # Routed shelves lead — unless the query turned out to be a name lookup:
    # the user wants the named thing ("New Order"), not the shelf whose title
    # shares its words ("New Releases").
    routed: List[BrowseItem] = []
    if route_task is not None:
        routed = await route_task
        if routed and any_name_lookup:
            logger.info("ai_search: name lookup %r — routed shelves hidden", query)
            routed = []

    sections = (
        routed + bm_sections + ai_cards + await _related_sections(ai_cards, by_source, cfg)
    )

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

    return _SourceResults(
        source=name,
        display_name=module.display_name(),
        candidates=candidates,
        ai_sections=ai_sections,
    )


def _source_best_match(
    src: _SourceResults, query: str, cfg: SearchConfig
) -> tuple[Optional[BrowseItem], bool]:
    """Build one source's BEST MATCH section and decide whether to hide its AI.

    Returns ``(section, is_name_lookup)``: the section (None if nothing cleared
    the cut-off) and whether the query is a near-exact whole-string match
    against one of this source's ARTIST matches — a pure name lookup ("jean
    michel jarre" -> "Jean-Michel Jarre") that should hide this source's
    suggestions. Only artists count: an album / playlist / track named like a
    mood ("Late Night Jazz") is more likely a discovery query, so it keeps the
    suggestions. A partial / extra-word match keeps them too ("workout music" ->
    "Workout"). The cut-off score is shared across sources.
    """
    items_by_id = {item.id.to_string: item for item in src.candidates}
    winners = assemble_best_match(
        [browse_item_to_entity(item) for item in src.candidates],
        query,
        cutoff=cfg.best_match_min_score,
        max_results=cfg.best_match_max_results,
    )
    section = _best_match_section(
        src.source,
        src.display_name,
        [items_by_id[w.id] for w in winners if w.id in items_by_id],
    )
    is_name_lookup = any(
        w.type == "artist"
        and full_match_score(query, w.name) >= cfg.ai_suppress_full_match_score
        for w in winners
    )
    return section, is_name_lookup


def _best_match_section(
    source: str, display_name: str, items: List[BrowseItem]
) -> Optional[BrowseItem]:
    """Wrap one source's ordered BEST MATCH winners (mixed entity types) in a
    flat TILE section, titled with the source's display name. None when empty
    so the UI renders no header."""
    if not items:
        return None
    cat = EntityId(id=f"best_match_{source}", type=EntityType.CATALOG, source="server")
    title = f"BEST MATCH · {display_name}"
    return BrowseItem(
        id=cat,
        name=title,
        subname="Top results for your search",
        can_browse=False,
        can_add=False,
        catalog=Catalog(
            id=cat,
            title=title,
            # Attributed to the source whose results these are; id.source is
            # "server" (assembled here), so the origin lives in `sources`.
            sources=sorted({it.id.source for it in items}),
            preview_config=Preview(
                type=PreviewType.TILE,
                content_type=PreviewContentType.CATALOG,
                icon="best_match",
                items_count=len(items),
            ),
        ),
        sections=items,
    )


# Related-artist get() lookups run in parallel batches of _RESOLVE_BATCH,
# each capped at _RESOLVE_TIMEOUT_S — bounding the extra search latency to
# n_batches × timeout even when a source hangs.
_RESOLVE_BATCH = 6
_RESOLVE_TIMEOUT_S = 3.0


async def _related_sections(
    ai_sections: List[BrowseItem],
    by_source: dict[str, InputModule],
    cfg: SearchConfig,
) -> List[BrowseItem]:
    """Derive Related Artists from the AI suggestion tracks.

    Rolls up the tracks inside every source's card (their union, in rank order)
    by artist, ranks them by suggestion count then first appearance, resolves
    the top ``related_max_results`` to full artist entities (the track stubs
    carry no image), and wraps them into a TILE section. Empty in, empty out —
    so when the suggestions were hidden, no Related row appears.
    """
    artist_pairs: list = []
    for card in ai_sections:
        for item in card.sections or []:
            track = item.track
            if track is None:
                continue
            # Prefer the track's own performer over the album artist, which can
            # be "Various Artists" on a compilation.
            artist = track.performer or (track.album.artist if track.album else None)
            if artist is not None:
                artist_pairs.append((artist.id.to_string, artist))

    artists = _rollup(artist_pairs, cfg.related_max_results)
    artists = await _resolve_artists(artists, by_source)

    sections: List[BrowseItem] = []
    artist_cards = [_artist_card(a) for a in artists]
    if artist_cards:
        sections.append(
            _related_catalog(
                "Related Artists", "artist", PreviewContentType.ARTIST, artist_cards
            )
        )
    return sections


async def _resolve_artists(
    artists: List[Artist], by_source: dict[str, InputModule]
) -> List[Artist]:
    """Swap each artist stub for its source's full entity, image included.

    The stubs come from ``track.performer``, which carries only id + name.
    Any failure keeps the stub: this row is decorative and must never break
    or stall the search response.
    """

    async def one(artist: Artist) -> Artist:
        module = by_source.get(artist.id.source)
        if module is None:
            return artist
        try:
            item = await asyncio.wait_for(
                module.get(artist.id), timeout=_RESOLVE_TIMEOUT_S
            )
        except Exception as e:
            logger.debug("related: could not resolve %s: %s", artist.id.to_string, e)
            return artist
        if item is not None and item.artist is not None:
            return item.artist
        return artist

    resolved: List[Artist] = []
    for start in range(0, len(artists), _RESOLVE_BATCH):
        batch = artists[start : start + _RESOLVE_BATCH]
        resolved.extend(await asyncio.gather(*(one(a) for a in batch)))
    return resolved


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
            # Rolled up across sources, so this can be several names.
            sources=sorted({it.id.source for it in items}),
            preview_config=Preview(
                type=PreviewType.TILE,
                content_type=content_type,
                icon=icon,
                items_count=len(items),
            ),
        ),
        sections=items,
    )
