"""get_all batches artist resolution into one /artists call and caches it."""

from kalinka_plugin_sdk.datamodel import EntityId, EntityType

import kalinka_plugin_jamendo.jamendo as jm
from kalinka_plugin_jamendo.config_model import JamendoConfig


class ArtistClient:
    """Returns an artist dict per requested id; records every request."""

    def __init__(self):
        self.calls = []

    async def request(self, path, params):
        self.calls.append((path, params))
        ids = str(params.get("id", "")).split()
        return [
            {"id": aid, "name": f"Artist {aid}", "image": f"https://img/{aid}"}
            for aid in ids
        ]


def _aid(local: str) -> EntityId:
    return EntityId(id=local, type=EntityType.ARTIST, source="jamendo")


def _make():
    client = ArtistClient()
    return jm.JamendoInputModule(JamendoConfig(client_id="x"), client), client


async def test_artists_resolve_in_one_request():
    module, client = _make()

    items = await module.get_all([_aid("1"), _aid("2"), _aid("3")])

    assert [it.name for it in items] == ["Artist 1", "Artist 2", "Artist 3"]
    assert len(client.calls) == 1
    path, params = client.calls[0]
    assert path == "artists" and params["id"] == "1 2 3"


async def test_cached_artists_skip_the_request():
    module, client = _make()
    await module.get_all([_aid("1"), _aid("2")])

    # Fully cached -> no request at all; order follows the ask.
    items = await module.get_all([_aid("2"), _aid("1")])
    assert [it.name for it in items] == ["Artist 2", "Artist 1"]
    assert len(client.calls) == 1

    # Partially cached -> only the missing id is requested.
    await module.get_all([_aid("1"), _aid("9")])
    assert len(client.calls) == 2
    assert client.calls[1][1]["id"] == "9"


async def test_batch_failure_returns_what_cache_has():
    module, client = _make()
    await module.get_all([_aid("1")])

    async def boom(path, params):
        raise RuntimeError("api down")

    client.request = boom
    items = await module.get_all([_aid("1"), _aid("2")])
    # The cached artist still resolves; the unresolvable one is omitted.
    assert [it.name for it in items] == ["Artist 1"]
