"""The search endpoints: a source's hits arrive annotated, and a source that
fails is a 503 naming it."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from kalinka_plugin_sdk.datamodel import (
    Artist,
    BrowseItem,
    BrowseItemList,
    EntityId,
    EntityType,
)
from kalinka_plugin_sdk.inputmodule import InputModule, SearchType

from kalinka_server.config_model import SearchConfig
from kalinka_server.search_route import register_search_routes


class _Module(InputModule):
    def __init__(self, name, failing=False):
        self._name = name
        self._failing = failing

    def module_name(self):
        return self._name

    async def search(self, type, query, offset=0, limit=50):
        if self._failing:
            raise RuntimeError("upstream down")
        if type is not SearchType.artist:
            return BrowseItemList(offset=offset, limit=limit, total=0, items=[])
        aid = EntityId(id="1", type=EntityType.ARTIST, source=self._name)
        item = BrowseItem(
            id=aid,
            name="The Beatles",
            can_browse=True,
            artist=Artist(id=aid, name="The Beatles"),
        )
        return BrowseItemList(offset=offset, limit=limit, total=1, items=[item])

    async def ai_search(self, query, offset=0, limit=50):
        if self._failing:
            raise RuntimeError("upstream down")
        return BrowseItemList(offset=offset, limit=limit, total=0, items=[])


MODULES = {"localfiles": _Module("localfiles"), "qobuz": _Module("qobuz", failing=True)}


def _resolve(sources):
    names = sources.split(",") if sources else list(MODULES)
    unknown = [name for name in names if name not in MODULES]
    if unknown:
        raise HTTPException(status_code=404, detail="No matching input modules found")
    return [MODULES[name] for name in names]


@pytest.fixture
def client():
    app = FastAPI()
    register_search_routes(app, _resolve, _resolve, SearchConfig)
    return TestClient(app)


def test_matches_arrive_annotated(client):
    response = client.get(
        "/search/matches", params={"query": "the beatles", "sources": "localfiles"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["match"] == {"tier": "exact", "score": 100.0}


def test_a_failing_source_is_503_naming_it(client):
    response = client.get(
        "/search/matches", params={"query": "the beatles", "sources": "qobuz"}
    )

    assert response.status_code == 503
    assert response.json()["detail"]["source"] == "qobuz"


def test_suggestions_fail_the_same_way(client):
    ok = client.get("/ai_search", params={"query": "calm", "sources": "localfiles"})
    down = client.get("/ai_search", params={"query": "calm", "sources": "qobuz"})
    assert (ok.status_code, down.status_code) == (200, 503)


def test_an_unknown_source_is_404(client):
    response = client.get(
        "/search/matches", params={"query": "the beatles", "sources": "nope"}
    )
    assert response.status_code == 404


def test_a_name_reaches_a_source_that_suggests_nothing():
    """Collections are searched by name but have no audio to suggest: the
    suggestion leg answers empty rather than reporting a missing source."""
    app = FastAPI()
    register_search_routes(
        app,
        lambda sources: [_Module("collections")],
        lambda sources: [],
        SearchConfig,
    )
    client = TestClient(app)

    matches = client.get(
        "/search/matches", params={"query": "the beatles", "sources": "collections"}
    )
    suggestions = client.get(
        "/ai_search", params={"query": "the beatles", "sources": "collections"}
    )

    assert matches.json()["total"] == 1
    assert suggestions.status_code == 200
    assert suggestions.json()["total"] == 0
