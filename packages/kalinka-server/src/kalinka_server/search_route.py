"""The search endpoints: literal name matches and semantic suggestions.

``sources`` is required on both. A client asks each source on its own so it
can lay each answer out as it lands and retry the one that did not, and one
listing merged over every source is not something it can lay out a piece at
a time.

Name matches may still name several sources at once — their tiers are
comparable, which is what lets a client merge them itself — while
suggestions take one source only: relevance scores from different sources
are not comparable at all, so there is no honest way to interleave them.

A source that fails is a 503 naming it, never a listing that looks complete
and is not.
"""

import logging
import time
from typing import Callable, List

from fastapi import FastAPI, HTTPException

from kalinka_plugin_sdk.datamodel import BrowseItemList, EmptyList
from kalinka_plugin_sdk.inputmodule import InputModule

from .browse_source import BrowseSource
from .config_model import SearchConfig
from .name_matches import collect_name_matches
from .source_failed import SourceFailed

logger = logging.getLogger(__name__)


def register_search_routes(
    app: FastAPI,
    resolve_browse_sources: Callable[[str], List[BrowseSource]],
    resolve_modules: Callable[[str], List[InputModule]],
    config: Callable[[], SearchConfig],
) -> None:
    """Mount the search endpoints on ``app``.

    Two resolvers, because the endpoints reach different halves of a source:
    a name is matched against everything browsable, while suggestions come
    only from a module that has audio to suggest. Both turn the ``sources``
    parameter into sources, raising for names neither knows. ``config`` is
    read per request so a settings change is seen without a restart.
    """

    @app.get("/search/matches")
    async def search_matches(query: str, sources: str) -> BrowseItemList:
        """Every hit the sources return for a name, ranked as one list — each
        annotated with why it stands where it does."""
        try:
            return await collect_name_matches(
                resolve_browse_sources(sources), query, config()
            )
        except SourceFailed as e:
            raise _unavailable(e)

    @app.get("/ai_search")
    async def ai_search(query: str, sources: str) -> BrowseItemList:
        """One source's semantic suggestions, as the source presented them.

        How many tracks a source may suggest is the server's
        ``ai_suggestions_limit``, not the caller's: relevance falls away past
        the first few dozen, so there is nothing to page to.
        """
        if "," in sources:
            raise HTTPException(
                status_code=400,
                detail="Ask one source at a time: suggestions are not merged",
            )
        modules = resolve_modules(sources)
        # A browsable source with no input module behind it — collections —
        # is searched by name but has no audio of its own to suggest.
        if not modules or not query.strip():
            return EmptyList(0, 0)
        try:
            return await _suggestions(
                modules[0], query, config().ai_suggestions_limit
            )
        except SourceFailed as e:
            raise _unavailable(e)


async def _suggestions(
    module: InputModule, query: str, limit: int
) -> BrowseItemList:
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
        name,
        len(result.items),
        time.monotonic() - started,
    )
    return result


def _unavailable(failure: SourceFailed) -> HTTPException:
    logger.warning("search: source %s unavailable: %r", failure.source, failure.cause)
    return HTTPException(
        status_code=503,
        detail={"source": failure.source, "reason": "Source unavailable"},
    )
