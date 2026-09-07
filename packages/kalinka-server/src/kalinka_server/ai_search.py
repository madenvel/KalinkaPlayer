"""Cross-source AI-search assembly.

``/ai_search`` runs every requested module's ``ai_search()`` and returns the
presentation-ready sections it hands back, one source after another — never
merged across sources, whose relevance scores are not comparable. A client
that wants each source to arrive on its own asks for one source per request.

  * **CATALOG ROUTES** — when the query names a browse shelf ("recently added
    to the library"), the matching root cards lead (see :mod:`query_router`).
    A surviving route means the query is catalog-shaped, not
    discovery-shaped, and mood-matched suggestions are noise under it, so the
    suggestion cards are then left out.
  * **SUGGESTIONS** — each module's ``ai_search()`` sections, verbatim, the
    user's own library first and the rest by name.

A module whose leg raises makes the whole request fail (:class:`SourceFailed`)
rather than quietly answering for the sources that worked: a client asking
per source can then say that source is unavailable, and offer a retry.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional, Sequence, TYPE_CHECKING

from kalinka_plugin_sdk.datamodel import BrowseItem, BrowseItemList, EmptyList
from kalinka_plugin_sdk.inputmodule import InputModule

from .config_model import SearchConfig
from .name_matches import SourceFailed

if TYPE_CHECKING:
    from .query_router import CatalogRouter

logger = logging.getLogger(__name__.split(".")[-1])


def _source_rank(name: str) -> tuple:
    return (0 if name == "localfiles" else 1, name)


async def assemble_ai_search(
    modules: Sequence[InputModule],
    query: str,
    offset: int,
    limit: int,
    cfg: Optional[SearchConfig] = None,
    router: "Optional[CatalogRouter]" = None,
) -> BrowseItemList:
    """Routed catalog shortcuts, then each source's suggestion sections.

    Raises:
        SourceFailed: a source's ``ai_search()`` raised.
    """
    cfg = cfg or SearchConfig()
    if not query.strip() or not modules:
        return EmptyList(offset, limit)

    # Routing overlaps with the fan-out; route() never raises. Restricted to
    # the sources this request targets.
    route_task = (
        asyncio.create_task(
            router.route(query, {m.module_name() for m in modules}, cfg)
        )
        if router is not None
        else None
    )
    ordered = sorted(modules, key=lambda m: _source_rank(m.module_name()))
    per_source = await asyncio.gather(
        *(_suggestions(module, query, cfg.ai_suggestions_limit) for module in ordered)
    )

    routed: List[BrowseItem] = await route_task if route_task is not None else []
    cards = [card for cards in per_source for card in cards]
    if routed and cards:
        logger.info("ai_search: %r routed to a catalog — suggestions hidden", query)
        cards = []

    sections = routed + cards
    return BrowseItemList(
        offset=offset,
        limit=limit,
        total=len(sections),
        items=sections[offset : offset + limit],
    )


async def _suggestions(module: InputModule, query: str, limit: int) -> List[BrowseItem]:
    name = module.module_name()
    started = time.monotonic()
    try:
        result = await module.ai_search(query, 0, limit)
    except Exception as e:
        logger.warning("ai_search failed for %s: %r", name, e)
        raise SourceFailed(name, e)
    # Logged always: a source that answered empty-success is otherwise
    # indistinguishable from one that was never asked.
    logger.info(
        "ai_search for %s: %d section(s) in %.2fs",
        name, len(result.items), time.monotonic() - started,
    )
    return list(result.items)
