"""The collections write API, over HTTP.

The store's own tests cover what a write leaves behind; these cover what a
client gets back — the id it can go to, and a refusal it can act on.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kalinka_plugin_sdk.datamodel import (
    BrowseItem,
    BrowseItemList,
    EntityId,
    EntityType,
    Genre,
)
from kalinka_plugin_sdk.inputmodule import TrackInfo

from kalinka_server.collections.route import register_collection_routes
from kalinka_server.collections.store import CollectionStore
from tests.collections_seed import track as _track


class FakeSource:
    """A source that holds one album of two tracks, so a container has
    something to expand into."""

    ALBUM = "kalinka:qobuz:album:al-1"

    async def browse(self, entity_id, offset=0, limit=50, filter=None):
        items = [
            BrowseItem(
                id=EntityId(id=local, type=EntityType.TRACK, source="qobuz"),
                name=local,
                can_browse=False,
                can_add=True,
            )
            for local in ("t1", "t2")
        ]
        return BrowseItemList(offset=offset, limit=limit, total=len(items), items=items)


class FakeModule:
    """Describes every track it is asked about, bar the ones it was told to
    stay silent on."""

    def __init__(self, silent_about=()):
        self.silent_about = set(silent_about)

    async def get_track_info(self, track_ids):
        return [
            TrackInfo(
                id=EntityId(id=local, type=EntityType.TRACK, source="qobuz"),
                source_retriever=_nothing,
                metadata=None
                if local in self.silent_about
                else _snapshot(local),
            )
            for local in track_ids
        ]


async def _nothing():
    raise AssertionError("A collection never resolves audio")


def _snapshot(local):
    snapshot = _track("qobuz", local, f"Track {local}", "A Band", "An Album", 100)
    snapshot.album.genres = [
        Genre(id=EntityId(id="7", type=EntityType.GENRE, source="qobuz"), name="Jazz")
    ]
    return snapshot


def _mount(app, store, module=None):
    register_collection_routes(
        app, store, lambda _: FakeSource(), lambda _: module or FakeModule()
    )


@pytest.fixture
async def client(tmp_path):
    store = CollectionStore(str(tmp_path / "collections.db"))
    await store.open()
    app = FastAPI()
    _mount(app, store)
    with TestClient(app) as client:
        yield client, store
    await store.close()


async def test_creating_answers_with_the_id_to_go_to(client):
    http, store = client

    response = http.post("/collections", json={"name": "Night Drive"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Night Drive"
    assert body["id"].startswith("kalinka:collections:playlist:")
    rows, total = await store.list_collections()
    assert (total, rows[0].name) == (1, "Night Drive")


async def test_a_name_of_spaces_is_no_name(client):
    http, _ = client

    response = http.post("/collections", json={"name": "   "})

    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "name"


async def test_a_name_is_taken_as_typed_minus_its_edges(client):
    http, store = client

    http.post("/collections", json={"name": "  Sunday Morning  "})

    rows, _ = await store.list_collections()
    assert rows[0].name == "Sunday Morning"


async def test_an_overlong_name_is_refused(client):
    http, _ = client

    response = http.post("/collections", json={"name": "x" * 500})

    assert response.status_code == 422


async def test_an_unavailable_file_says_so_rather_than_lying(tmp_path):
    path = tmp_path / "collections.db"
    path.write_bytes(b"not a database at all")
    store = CollectionStore(str(path))
    await store.open()
    app = FastAPI()
    _mount(app, store)

    with TestClient(app) as http:
        response = http.post("/collections", json={"name": "Nowhere"})

    assert response.status_code == 503


async def test_renaming_answers_with_the_name_that_stuck(client):
    http, store = client
    made = http.post("/collections", json={"name": "Night Drive"}).json()

    response = http.patch(
        f"/collections/{made['id']}", json={"name": "  Night Drives  "}
    )

    assert response.status_code == 200
    assert response.json() == {"id": made["id"], "name": "Night Drives"}
    rows, _ = await store.list_collections()
    assert rows[0].name == "Night Drives"


async def test_renaming_what_is_not_there_is_a_miss(client):
    http, _ = client

    response = http.patch(
        "/collections/kalinka:collections:playlist:nobody", json={"name": "Ghost"}
    )

    assert response.status_code == 404


async def test_an_id_from_another_source_names_no_collection(client):
    http, store = client
    http.post("/collections", json={"name": "Night Drive"})
    rows, _ = await store.list_collections()

    response = http.patch(
        f"/collections/kalinka:qobuz:playlist:{rows[0].id}", json={"name": "Theirs"}
    )

    assert response.status_code == 404
    assert (await store.get_collection(rows[0].id)).name == "Night Drive"


async def test_renaming_to_nothing_is_refused(client):
    http, _ = client
    made = http.post("/collections", json={"name": "Night Drive"}).json()

    response = http.patch(f"/collections/{made['id']}", json={"name": "  "})

    assert response.status_code == 422


async def test_adding_tracks_says_how_many_landed(client):
    http, store = client
    made = http.post("/collections", json={"name": "Night Drive"}).json()

    response = http.post(
        f"/collections/{made['id']}/entries",
        json={"items": ["kalinka:qobuz:track:t1", "kalinka:qobuz:track:t2"]},
    )

    assert response.status_code == 200
    assert response.json() == {"added": 2, "already_there": 0}
    rows, total = await store.list_entries(made["id"].split(":")[-1])
    assert total == 2
    assert [row.entity_id for row in rows] == [
        "kalinka:qobuz:track:t1",
        "kalinka:qobuz:track:t2",
    ]


async def test_a_container_is_expanded_into_its_tracks(client):
    http, store = client
    made = http.post("/collections", json={"name": "Night Drive"}).json()

    response = http.post(
        f"/collections/{made['id']}/entries", json={"items": [FakeSource.ALBUM]}
    )

    assert response.json() == {"added": 2, "already_there": 0}
    rows, _ = await store.list_entries(made["id"].split(":")[-1])
    assert [row.entity_id for row in rows] == [
        "kalinka:qobuz:track:t1",
        "kalinka:qobuz:track:t2",
    ]


async def test_what_is_already_there_is_reported_not_repeated(client):
    http, _ = client
    made = http.post("/collections", json={"name": "Night Drive"}).json()
    http.post(
        f"/collections/{made['id']}/entries",
        json={"items": ["kalinka:qobuz:track:t1"]},
    )

    response = http.post(
        f"/collections/{made['id']}/entries",
        json={"items": ["kalinka:qobuz:track:t1", "kalinka:qobuz:track:t2"]},
    )

    assert response.json() == {"added": 1, "already_there": 1}


async def test_a_genre_is_kept_under_its_name_not_the_sources_id(client):
    http, store = client
    made = http.post("/collections", json={"name": "Night Drive"}).json()

    http.post(
        f"/collections/{made['id']}/entries",
        json={"items": ["kalinka:qobuz:track:t1"]},
    )

    genres = await store.genres_of(made["id"].split(":")[-1])
    assert [(value.id, value.name) for value in genres] == [("jazz", "Jazz")]


async def test_a_track_the_source_will_not_describe_is_left_out(tmp_path):
    store = CollectionStore(str(tmp_path / "collections.db"))
    await store.open()
    app = FastAPI()
    _mount(app, store, module=FakeModule(silent_about=["t2"]))

    with TestClient(app) as http:
        made = http.post("/collections", json={"name": "Night Drive"}).json()
        response = http.post(
            f"/collections/{made['id']}/entries",
            json={"items": ["kalinka:qobuz:track:t1", "kalinka:qobuz:track:t2"]},
        )

    assert response.json() == {"added": 1, "already_there": 0}
    await store.close()


async def test_adding_to_what_is_not_there_is_a_miss(client):
    http, _ = client

    response = http.post(
        "/collections/kalinka:collections:playlist:nobody/entries",
        json={"items": ["kalinka:qobuz:track:t1"]},
    )

    assert response.status_code == 404


async def test_adding_to_another_sources_id_is_a_miss(client):
    http, _ = client

    response = http.post(
        "/collections/kalinka:qobuz:playlist:p1/entries",
        json={"items": ["kalinka:qobuz:track:t1"]},
    )

    assert response.status_code == 404


async def test_adding_nothing_is_refused(client):
    http, _ = client
    made = http.post("/collections", json={"name": "Night Drive"}).json()

    response = http.post(f"/collections/{made['id']}/entries", json={"items": []})

    assert response.status_code == 422


async def test_replacing_leaves_only_what_was_sent(client):
    http, store = client
    made = http.post("/collections", json={"name": "Night Drive"}).json()
    http.post(
        f"/collections/{made['id']}/entries",
        json={"items": ["kalinka:qobuz:track:t1", "kalinka:qobuz:track:t2"]},
    )

    response = http.put(
        f"/collections/{made['id']}/entries",
        json={"items": ["kalinka:qobuz:track:t2"]},
    )

    assert response.status_code == 200
    assert response.json() == {"added": 1, "dropped": 2}
    rows, total = await store.list_entries(made["id"].split(":")[-1])
    assert total == 1
    assert [row.entity_id for row in rows] == ["kalinka:qobuz:track:t2"]


async def test_replacing_what_is_not_there_is_a_miss(client):
    http, _ = client

    response = http.put(
        "/collections/kalinka:collections:playlist:nobody/entries",
        json={"items": ["kalinka:qobuz:track:t1"]},
    )

    assert response.status_code == 404


async def test_replacing_with_nothing_is_refused(client):
    """Emptying a collection is its own write, not a replace with no tracks."""
    http, _ = client
    made = http.post("/collections", json={"name": "Night Drive"}).json()

    response = http.put(f"/collections/{made['id']}/entries", json={"items": []})

    assert response.status_code == 422
