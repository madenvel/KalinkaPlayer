#!/usr/bin/env python3
"""Wikidata artist images.

Regression cover for a shipped defect: the module called
``raise_if_service_unavailable`` without importing it, so every artist image
lookup died on a NameError that the plugin's own ``except Exception`` turned
into a quiet "no image". It stayed invisible for the whole library because
Deezer runs next and papered over most of it — every artist Deezer also
lacked, such as Виктор Цой, simply had no picture. These tests reach the
throttle path, which cannot be reached at all unless the name resolves.
"""

import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.enricher.enricher_plugin import (
    TransientEnrichmentError,
)
from kalinka_plugin_localfiles.enricher.wikidata_plugin import WikidataPlugin

ARTIST = {"id": "artist_1", "name": "Виктор Цой", "mbid": "mbid-tsoi"}

_MB_WITH_WIKIDATA = {
    "artist": {
        "url-relation-list": [
            {"type": "wikidata", "target": "https://www.wikidata.org/wiki/Q487125"}
        ]
    }
}


class FakeResponse:
    def __init__(self, status_code, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.requested = []

    async def get(self, url, params=None):
        self.requested.append(url)
        return self._responses.pop(0) if self._responses else FakeResponse(404)


def _plugin(tmp_path) -> WikidataPlugin:
    config = LocalFilesConfig(
        db_path=str(tmp_path / "db.sqlite"),
        artwork_path=str(tmp_path / "artwork"),
    )
    return WikidataPlugin(config, db_manager=None)


@pytest.fixture
def mb_says_wikidata(monkeypatch):
    monkeypatch.setattr(
        "kalinka_plugin_localfiles.enricher.wikidata_plugin."
        "musicbrainzngs.get_artist_by_id",
        lambda *a, **kw: _MB_WITH_WIKIDATA,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_throttled_wikidata_is_transient(tmp_path, mb_says_wikidata, status):
    """The whole point of the missing import: an outage must stay pending
    rather than be recorded as "this artist has no picture"."""
    p = _plugin(tmp_path)
    p.async_client = FakeClient(FakeResponse(status))

    with pytest.raises(TransientEnrichmentError):
        await p.enrich_artist(dict(ARTIST))


@pytest.mark.asyncio
async def test_an_artist_wikidata_has_no_picture_of_is_a_quiet_no(
    tmp_path, mb_says_wikidata
):
    p = _plugin(tmp_path)
    p.async_client = FakeClient(FakeResponse(200, {"claims": {}}))

    assert await p.enrich_artist(dict(ARTIST)) is None


@pytest.mark.asyncio
async def test_an_artist_without_an_mbid_is_never_looked_up(tmp_path):
    p = _plugin(tmp_path)
    p.async_client = FakeClient()

    assert await p.enrich_artist({"id": "artist_1", "name": "Nobody"}) is None
    assert p.async_client.requested == []


@pytest.mark.asyncio
async def test_an_artist_musicbrainz_does_not_link_is_a_quiet_no(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "kalinka_plugin_localfiles.enricher.wikidata_plugin."
        "musicbrainzngs.get_artist_by_id",
        lambda *a, **kw: {"artist": {"url-relation-list": []}},
    )
    p = _plugin(tmp_path)
    p.async_client = FakeClient()

    assert await p.enrich_artist(dict(ARTIST)) is None
    assert p.async_client.requested == []


def test_the_plugin_identifies_itself_to_musicbrainz(tmp_path):
    """It reaches MusicBrainz for the Wikidata link, so it cannot rely on the
    MusicBrainz plugin being enabled to have set the user agent."""
    import musicbrainzngs
    import musicbrainzngs.musicbrainz as mb

    musicbrainzngs.set_useragent("unset", "0", "")
    _plugin(tmp_path)
    assert mb._useragent.startswith("Kalinka/1.0")
