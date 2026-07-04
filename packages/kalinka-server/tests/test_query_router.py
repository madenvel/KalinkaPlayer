"""Tests for catalog routing (kalinka_server.query_router).

The FakeEmbedder is a deterministic bag-of-words hasher: cosine similarity is
token overlap. Coarser than MiniLM but shape-compatible, so the tests exercise
the real scoring pipeline — variants, decoys, floor/margin, module-mention
filter — without model assets.
"""

import zlib

import numpy as np
import pytest

from kalinka_plugin_sdk.datamodel import (
    BrowseItem,
    BrowseItemList,
    Catalog,
    EntityId,
    EntityType,
)
from kalinka_plugin_sdk.inputmodule import SearchType

from kalinka_server.ai_search import assemble_ai_search
from kalinka_server.config_model import SearchConfig
from kalinka_server.query_router import CatalogRouter

from .test_ai_search import FakeModule, _artist_item, _track_item

_DIM = 64


class FakeEmbedder:
    """Bag-of-words embedding: v[crc32(token) % DIM] += 1, L2-normalized."""

    model_id = "all-MiniLM-L6-v2"
    model_version = 1
    dim = _DIM

    def __init__(self):
        self.embed_calls = 0

    async def available(self) -> bool:
        return True

    async def embed(self, texts):
        self.embed_calls += 1
        out = []
        for text in texts:
            v = np.zeros(_DIM, np.float32)
            for token in text.lower().split():
                v[zlib.crc32(token.strip(".,!?").encode()) % _DIM] += 1.0
            norm = float(np.linalg.norm(v))
            out.append(v / norm if norm > 0 else v)
        return out


def _shelf(source: str, local: str, title: str, description=None) -> BrowseItem:
    cid = EntityId(id=local, type=EntityType.CATALOG, source=source)
    return BrowseItem(
        id=cid,
        name=title,
        can_browse=True,
        catalog=Catalog(id=cid, title=title, description=description),
    )


class RoutableModule(FakeModule):
    """FakeModule that serves a root catalog of shelves, and one preview track
    when browsed into by a shelf id (so routed cards get inline content)."""

    def __init__(self, name, shelves, display=None, **kw):
        super().__init__(name, **kw)
        self._shelves = shelves
        self._display = display or name

    def display_name(self) -> str:
        return self._display

    async def browse(self, entity_id, offset=0, limit=50, genre_ids=[]):
        if entity_id.id == "root":
            items = list(self._shelves)
        else:
            # Preview content for a shelf — one canned track per shelf.
            tid = EntityId(id=f"{entity_id.id}-t1", type=EntityType.TRACK,
                           source=entity_id.source)
            items = [BrowseItem(id=tid, name=f"{entity_id.id} track", can_add=True)]
        return BrowseItemList(
            offset=offset, limit=limit, total=len(items), items=items,
        )


def _library():
    return RoutableModule(
        "localfiles",
        [
            _shelf("localfiles", "recent", "Recently Added", "Recently added tracks"),
            _shelf("localfiles", "albums", "My Albums", "Browse your album collection"),
        ],
        display="Local files",
    )


def _jamendo():
    return RoutableModule(
        "Jamendo",  # module_name(); the plugin key differs ("jamendo")
        [
            _shelf("jamendo", "new-releases", "New Releases"),
            _shelf("jamendo", "popular-tracks", "Popular Tracks"),
        ],
        source="jamendo",
    )


async def _built_router(*pairs):
    router = CatalogRouter(FakeEmbedder())
    await router.rebuild(list(pairs))
    return router


# ---------------------------------------------------------------------------
# Routing decisions
# ---------------------------------------------------------------------------


async def test_catalog_intent_query_routes_to_shelf():
    router = await _built_router(("localfiles", _library()), ("jamendo", _jamendo()))

    routed = await router.route("recently added to the library", None, SearchConfig())

    assert routed, "catalog-intent query must route"
    top = routed[0]
    # The module's own card, retitled with the source like BEST MATCH sections.
    assert top.name == "Recently Added · Local files"
    assert top.catalog.title == "Recently Added · Local files"
    assert top.id.source == "localfiles" and top.id.id == "recent"
    # Self-contained: preview items pulled from the shelf are inline, so the
    # search feed (which drops empty-section cards) renders it.
    assert top.sections and top.sections[0].id.id == "recent-t1"


async def test_mood_query_falls_through_to_search():
    router = await _built_router(("localfiles", _library()), ("jamendo", _jamendo()))

    routed = await router.route(
        "calm melancholic music for tonight", None, SearchConfig()
    )

    assert routed == []


async def test_module_mention_restricts_to_that_module():
    # Both sources have a "New Releases" shelf; naming jamendo must exclude
    # the other one no matter how well it scores.
    lib = _library()
    lib._shelves.append(_shelf("localfiles", "new", "New Releases"))
    router = await _built_router(("localfiles", lib), ("jamendo", _jamendo()))

    routed = await router.route("new releases on jamendo", None, SearchConfig())

    assert routed
    assert {c.id.source for c in routed} == {"jamendo"}


async def test_allowed_sources_matches_module_name_not_plugin_key():
    # assemble_ai_search passes module_name() values ("Jamendo"), which differ
    # from the plugin key ("jamendo") the routes are registered under.
    router = await _built_router(("localfiles", _library()), ("jamendo", _jamendo()))
    query = "new releases this week"

    only_jamendo = await router.route(query, {"Jamendo"}, SearchConfig())
    no_sources = await router.route(query, set(), SearchConfig())

    assert only_jamendo and {c.id.source for c in only_jamendo} == {"jamendo"}
    assert no_sources == []


async def test_route_cap_and_ordering():
    router = await _built_router(("jamendo", _jamendo()))
    cfg = SearchConfig(route_max_results=1)

    routed = await router.route("popular tracks on jamendo", None, cfg)

    assert len(routed) == 1
    assert routed[0].id.id == "popular-tracks"


async def test_router_off_without_embedder_or_before_rebuild():
    no_embedder = CatalogRouter(None)
    await no_embedder.rebuild([("localfiles", _library())])
    assert await no_embedder.route("recently added", None, SearchConfig()) == []

    not_built = CatalogRouter(FakeEmbedder())
    assert await not_built.route("recently added", None, SearchConfig()) == []


async def test_empty_shelf_is_dropped():
    # A shelf that browses empty (e.g. an empty library) carries no preview,
    # so it must not be returned as a blank card the feed would drop anyway.
    class EmptyPreviewModule(RoutableModule):
        async def browse(self, entity_id, offset=0, limit=50, genre_ids=[]):
            if entity_id.id == "root":
                return await super().browse(entity_id, offset, limit)
            return BrowseItemList(offset=offset, limit=limit, total=0, items=[])

    lib = EmptyPreviewModule(
        "localfiles",
        [_shelf("localfiles", "recent", "Recently Added", "Recently added tracks")],
        display="Local files",
    )
    router = await _built_router(("localfiles", lib))

    assert await router.route("recently added to the library", None, SearchConfig()) == []


async def test_broken_module_degrades_to_no_routes_for_it():
    class BrokenModule(RoutableModule):
        async def browse(self, *a, **kw):
            raise RuntimeError("boom")

    router = await _built_router(
        ("localfiles", _library()),
        ("jamendo", BrokenModule("Jamendo", [], source="jamendo")),
    )

    routed = await router.route("recently added to the library", None, SearchConfig())
    assert routed and routed[0].id.source == "localfiles"


# ---------------------------------------------------------------------------
# ai_search assembly integration
# ---------------------------------------------------------------------------


async def test_assemble_prepends_routed_shelf():
    lib = _library()
    router = await _built_router(("localfiles", lib))

    result = await assemble_ai_search(
        [lib], "recently added to the library", 0, 10, router=router
    )

    assert result.items and result.items[0].name == "Recently Added · Local files"
    # The feed requires inline sections; the prepended shelf must carry them.
    assert result.items[0].sections


async def test_route_hides_ai_suggestion_cards():
    # A surviving route marks the query catalog-shaped: mood-matched AI
    # suggestion cards are hidden, while without a route they show as usual.
    lib = _library()
    lib._ai = [_track_item("localfiles", "t1", "Some Track")]
    router = await _built_router(("localfiles", lib))

    routed = await assemble_ai_search(
        [lib], "recently added to the library", 0, 10, router=router
    )
    unrouted = await assemble_ai_search(
        [lib], "dreamy shoegaze wall of sound", 0, 10, router=router
    )

    routed_names = [it.name for it in routed.items]
    assert routed_names[0] == "Recently Added · Local files"
    assert "AI SUGGESTIONS" not in routed_names, routed_names
    assert "AI SUGGESTIONS" in [it.name for it in unrouted.items]


async def test_assemble_name_lookup_vetoes_routed_shelf():
    # "New Order" is a band; its shelf-vocabulary words would route to the
    # "New Releases" shelf, but the full-name BEST MATCH must suppress that.
    jam = _jamendo()
    jam._search = {SearchType.artist: [_artist_item("jamendo", "a1", "New Order")]}
    router = await _built_router(("jamendo", jam))
    # The bag-of-words fake scores this trap 0.50 where real MiniLM gives
    # 0.58; lower the floor so the test still exercises the veto, not the
    # floor.
    cfg = SearchConfig(route_min_similarity=45)

    # Sanity: without the veto context, the trap query really does route.
    assert await router.route("new order", None, cfg)

    result = await assemble_ai_search([jam], "new order", 0, 10, cfg, router=router)

    names = [it.name for it in result.items]
    assert not any("New Releases" in n for n in names), names
    assert any("BEST MATCH" in n for n in names), names


async def test_assemble_without_router_unchanged():
    lib = _library()
    result = await assemble_ai_search([lib], "recently added", 0, 10)
    assert all("·" not in (it.name or "") for it in result.items)
