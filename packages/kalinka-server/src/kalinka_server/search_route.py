"""The search endpoints: literal name matches and semantic suggestions.

Both take ``sources`` so a client can ask one source at a time and lay each
answer out as it lands. A source that fails is a 503 naming it, never a
listing that looks complete and is not.
"""

import logging
from typing import Callable, List, Optional

from fastapi import FastAPI, HTTPException

from kalinka_plugin_sdk.datamodel import BrowseItemList
from kalinka_plugin_sdk.inputmodule import InputModule

from .ai_search import assemble_ai_search
from .config_model import SearchConfig
from .name_matches import SourceFailed, collect_name_matches
from .query_router import CatalogRouter

logger = logging.getLogger(__name__)


def register_search_routes(
    app: FastAPI,
    resolve_modules: Callable[[Optional[str]], List[InputModule]],
    config: Callable[[], SearchConfig],
    router: Callable[[], Optional[CatalogRouter]],
) -> None:
    """Mount the search endpoints on ``app``.

    ``resolve_modules`` turns the ``sources`` parameter into modules, raising
    for names it does not know; ``config`` and ``router`` are read per request
    so a settings change or a rebuilt router is seen without a restart.
    """

    @app.get("/search/matches")
    async def search_matches(
        query: str, sources: Optional[str] = None
    ) -> BrowseItemList:
        """Every hit the sources return for a name, ranked as one list — each
        annotated with why it stands where it does."""
        try:
            return await collect_name_matches(resolve_modules(sources), query, config())
        except SourceFailed as e:
            raise _unavailable(e)

    @app.get("/ai_search")
    async def ai_search(
        query: str,
        offset: int = 0,
        limit: int = 10,
        sources: Optional[str] = None,
    ) -> BrowseItemList:
        """Semantic suggestions from each source, as sections to render."""
        try:
            return await assemble_ai_search(
                resolve_modules(sources),
                query,
                offset,
                limit,
                config(),
                router=router(),
            )
        except SourceFailed as e:
            raise _unavailable(e)


def _unavailable(failure: SourceFailed) -> HTTPException:
    logger.warning("search: source %s unavailable: %r", failure.source, failure.cause)
    return HTTPException(
        status_code=503,
        detail={"source": failure.source, "reason": "Source unavailable"},
    )
