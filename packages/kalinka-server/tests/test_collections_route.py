"""The collections write API, over HTTP.

The store's own tests cover what a write leaves behind; these cover what a
client gets back — the id it can go to, and a refusal it can act on.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kalinka_server.collections.route import register_collection_routes
from kalinka_server.collections.store import CollectionStore


@pytest.fixture
async def client(tmp_path):
    store = CollectionStore(str(tmp_path / "collections.db"))
    await store.open()
    app = FastAPI()
    register_collection_routes(app, store)
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
    register_collection_routes(app, store)

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
