"""Cover Art Archive plugin: real covers for MB-matched albums no other
source has art for — the Deezer-less free/CC releases. It treats a
procedurally generated placeholder as absence so a later pass upgrades it,
and it never speaks for albums without a MusicBrainz id.
"""

from __future__ import annotations

import io
import os

import httpx
import pytest
from PIL import Image

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.enricher.coverartarchive_plugin import (
    CoverArtArchivePlugin,
)
from kalinka_plugin_localfiles.enricher.enricher_plugin import (
    TransientEnrichmentError,
)


def _plugin(tmp_path) -> CoverArtArchivePlugin:
    config = LocalFilesConfig(
        db_path=str(tmp_path / "db.sqlite"),
        artwork_path=str(tmp_path / "artwork"),
    )
    return CoverArtArchivePlugin(config, db_manager=None)


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), (200, 40, 40)).save(buf, "JPEG")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content


class FakeClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.requested: list[str] = []

    async def get(self, url):
        self.requested.append(url)
        if self._error is not None:
            raise self._error
        return self._response


ALBUM = {"id": "album_1", "title": "Autumn prelude", "mbid": "mbid-1"}


class TestScope:
    def test_albums_only(self, tmp_path):
        p = _plugin(tmp_path)
        assert p.can_enrich_album()
        assert not p.can_enrich_artist()
        assert not p.can_enrich_track()


@pytest.mark.asyncio
async def test_fetches_and_saves_cover_for_matched_album(tmp_path):
    p = _plugin(tmp_path)
    p.async_client = FakeClient(FakeResponse(200, _jpeg()))

    result = await p.enrich_album(dict(ALBUM))

    assert result == {"updates": {"image_url": "album_1", "image_generated": 0}}
    assert "mbid-1" in p.async_client.requested[0]
    for suffix in ("thumbnail", "small", "large"):
        assert os.path.exists(tmp_path / "artwork" / "album" / f"album_1_{suffix}.jpg")


@pytest.mark.asyncio
async def test_real_cover_is_left_alone_but_placeholder_is_upgraded(tmp_path):
    p = _plugin(tmp_path)
    p.async_client = FakeClient(FakeResponse(200, _jpeg()))

    real = dict(ALBUM, image_url="album_1")
    assert await p.enrich_album(real) is None
    assert p.async_client.requested == []

    generated = dict(ALBUM, image_url="album_1", image_generated=1)
    result = await p.enrich_album(generated)
    assert result is not None and result["updates"]["image_generated"] == 0


@pytest.mark.asyncio
async def test_album_without_mbid_is_skipped(tmp_path):
    p = _plugin(tmp_path)
    p.async_client = FakeClient(FakeResponse(200, _jpeg()))

    assert await p.enrich_album({"id": "album_1", "title": "No match"}) is None
    assert p.async_client.requested == []


@pytest.mark.asyncio
async def test_archive_without_art_is_a_quiet_no(tmp_path):
    """404 means the archive has nothing — a verdict, not an outage."""
    p = _plugin(tmp_path)
    p.async_client = FakeClient(FakeResponse(404))

    assert await p.enrich_album(dict(ALBUM)) is None


@pytest.mark.asyncio
async def test_unreachable_archive_is_transient(tmp_path):
    """Network failures defer the row instead of ruling on it, and feed the
    per-service stand-down like every other source."""
    for failure in (
        FakeClient(error=httpx.ConnectError("boom")),
        FakeClient(FakeResponse(503)),
    ):
        p = _plugin(tmp_path)
        p.async_client = failure
        with pytest.raises(TransientEnrichmentError):
            await p.enrich_album(dict(ALBUM))


@pytest.mark.asyncio
async def test_undecodable_image_returns_no_update(tmp_path):
    p = _plugin(tmp_path)
    p.async_client = FakeClient(FakeResponse(200, b"not an image"))

    assert await p.enrich_album(dict(ALBUM)) is None
