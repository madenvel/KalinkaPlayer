"""Offline unit tests for the Jamendo mapping/browse logic.

These stub JamendoClient.request so no network or client_id is needed.
"""

import pytest

from kalinka_plugin_sdk.datamodel import EntityType
from kalinka_plugin_sdk.inputmodule import SearchType, TrackUrl

from kalinka_plugin_jamendo.config_model import JamendoConfig, JamendoAudioFormat
from kalinka_plugin_jamendo import jamendo as jm


class FakeClient:
    """Records the last (path, params) and returns canned results."""

    def __init__(self, results):
        self._results = results
        self.calls = []

    async def request(self, path, params):
        self.calls.append((path, params))
        return self._results


def make_module(results, fmt=JamendoAudioFormat.FLAC):
    config = JamendoConfig(client_id="x", audio_format=fmt)
    module = jm.JamendoInputModule(config, FakeClient(results))
    return module


TRACK = {
    "id": "100",
    "name": "Sunrise",
    "duration": "212",
    "artist_id": "10",
    "artist_name": "The Band",
    "album_id": "5",
    "album_name": "Mornings",
    "album_image": "https://usercontent.jamendo.com?type=album&id=5&width=300",
    "audio": "https://prod.jamendo.com/?trackid=100&format=flac",
}

ALBUM = {
    "id": "5",
    "name": "Mornings",
    "artist_id": "10",
    "artist_name": "The Band",
    "image": "https://usercontent.jamendo.com?type=album&id=5&width=300",
}

ARTIST = {
    "id": "10",
    "name": "The Band",
    "image": "https://usercontent.jamendo.com?type=artist&id=10&width=300",
}

PLAYLIST = {
    "id": "7",
    "name": "Chill",
    "user_id": "99",
    "user_name": "dj",
    "tracks": [TRACK, TRACK],
}


def test_format_code_mapping():
    m = make_module([], fmt=JamendoAudioFormat.MP3_96)
    assert m.audio_format == "mp31"
    assert m.audio_mime == "audio/mpeg"


@pytest.mark.asyncio
async def test_search_tracks():
    m = make_module([TRACK])
    res = await m.search(SearchType.track, "sunrise", offset=0, limit=50)
    assert res.items[0].track.title == "Sunrise"
    assert res.items[0].track.id.type == EntityType.TRACK
    assert res.items[0].track.id.source == "jamendo"
    assert res.items[0].subname == "The Band"
    # audioformat must be forwarded for track search
    assert m.client.calls[0][1]["audioformat"] == "flac"


@pytest.mark.asyncio
async def test_search_albums_have_sections():
    m = make_module([ALBUM])
    res = await m.search(SearchType.album, "mornings")
    item = res.items[0]
    assert item.can_browse is True
    assert item.album.title == "Mornings"
    assert item.sections and item.sections[0].name == "Tracks"


@pytest.mark.asyncio
async def test_estimated_total_full_page_bumps():
    # A full page (count == limit) should advertise more pages.
    m = make_module([TRACK] * 2)
    res = await m.search(SearchType.track, "x", offset=0, limit=2)
    assert res.total == 4  # offset(0) + count(2) + limit(2)


@pytest.mark.asyncio
async def test_root_catalog():
    m = make_module([])
    root = jm.catalog_id("root")
    res = await m.browse(root)
    slugs = {i.id.id for i in res.items}
    assert {"popular-tracks", "new-releases", "popular-artists"} <= slugs
    # Root is built locally, no API call.
    assert m.client.calls == []


@pytest.mark.asyncio
async def test_browse_album_paginates_tracks():
    album_with_tracks = {**ALBUM, "tracks": [TRACK, TRACK, TRACK]}
    m = make_module([album_with_tracks])
    res = await m.browse(jm.album_id("5"), offset=1, limit=1)
    assert res.total == 3
    assert len(res.items) == 1


@pytest.mark.asyncio
async def test_browse_artist_paginates_albums():
    # artists/albums nests all albums under one artist; we paginate locally.
    artist_with_albums = {**ARTIST, "albums": [ALBUM, ALBUM, ALBUM]}
    m = make_module([artist_with_albums])
    res = await m.browse(jm.artist_id("10"), offset=2, limit=5)
    assert res.total == 3
    assert len(res.items) == 1
    # No offset/limit forwarded to the API — full album list is fetched.
    assert "offset" not in m.client.calls[0][1]


@pytest.mark.asyncio
async def test_get_track_info_preserves_order_and_url():
    t2 = {**TRACK, "id": "200", "name": "Dusk"}
    m = make_module([TRACK, t2])
    infos = await m.get_track_info(["200", "100"])
    assert [i.id.id for i in infos] == ["200", "100"]
    url = await infos[0].link_retriever()
    assert isinstance(url, TrackUrl)
    assert url.format == "audio/flac"
    assert "trackid=" in url.url


@pytest.mark.asyncio
async def test_get_playlist():
    m = make_module([PLAYLIST])
    item = await m.get(jm.playlist_id("7"))
    assert item.playlist.track_count == 2
    assert item.playlist.owner.name == "dj"


@pytest.mark.asyncio
async def test_unsupported_get_type_raises():
    m = make_module([])
    with pytest.raises(ValueError):
        await m.get(jm.user_id("1"))


@pytest.mark.asyncio
async def test_get_state_reports_missing_client_id():
    from types import SimpleNamespace

    from kalinka_plugin_sdk import ModuleHealthState
    from kalinka_plugin_jamendo.module_setup import KalinkaPluginJamendo

    plugin = KalinkaPluginJamendo()

    # No context yet (not set up) -> treated as missing key -> ERROR.
    state = await plugin.get_state()
    assert state.state == ModuleHealthState.ERROR
    assert "client_id" in state.message

    # Empty client_id -> ERROR with the registration URL.
    plugin._context = SimpleNamespace(config=SimpleNamespace(client_id=""))
    state = await plugin.get_state()
    assert state.state == ModuleHealthState.ERROR
    assert "devportal.jamendo.com" in state.message

    # Configured client_id -> READY.
    plugin._context = SimpleNamespace(config=SimpleNamespace(client_id="abc"))
    state = await plugin.get_state()
    assert state.state == ModuleHealthState.READY


@pytest.mark.asyncio
async def test_stubs_are_graceful():
    m = make_module([])
    assert (await m.list_genre()).total == 0
    assert (await m.get_favorite_ids()).tracks == []
    assert (await m.list_favorite(SearchType.track, "")).total == 0
    assert (await m.playlist_user_list()).total == 0
    assert await m.get_resource_path("x") is None
