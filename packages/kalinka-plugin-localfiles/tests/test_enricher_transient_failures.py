#!/usr/bin/env python3
"""A failure that says nothing about the entity — transport-level (DNS,
refused connection, timeout) or a service overload the client retried out —
must leave it pending for the next enrichment cycle, never recorded as
FAILED or ENRICHED-local-only. A response that speaks about the entity
(found, 404, …) is a verdict and is recorded as before.
"""

import urllib.error

import httpx
import musicbrainzngs
import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.enricher.deezer_plugin import DeezerPlugin
from kalinka_plugin_localfiles.enricher.enricher import MetadataEnricher
from kalinka_plugin_localfiles.enricher.enricher_plugin import (
    TransientEnrichmentError,
    raise_if_service_unavailable,
    raise_musicbrainz_unreachable,
)


class FakeDb:
    def __init__(self, albums):
        self.pending_albums = list(albums)
        self.saved = {}

    async def get_non_enriched_artists(self, limit=1):
        return []

    async def get_non_enriched_albums(self, limit=1):
        return self.pending_albums[:limit]

    async def get_non_enriched_tracks(self, limit=1):
        return []

    async def update_album(self, eid, data):
        self.saved[eid] = data.get("enriched")
        self.pending_albums = [a for a in self.pending_albums if a["id"] != eid]

    async def record_claim(self, *_a, **_k):
        pass

    async def get_resolved_origin(self, *_a, **_k):
        return None

    async def record_resolved_origin(self, *_a, **_k):
        pass

    async def get_album_track_tags(self, *_a, **_k):
        return []


class OfflinePlugin:
    runs_after_resolution = False

    def can_enrich_artist(self):
        return False

    def can_enrich_album(self):
        return True

    def can_enrich_track(self):
        return False

    async def enrich_album(self, album):
        raise TransientEnrichmentError("MusicBrainz is unreachable: dns down")


def _enricher(db):
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.db_manager = db
    enr.plugins = [OfflinePlugin()]
    return enr


@pytest.mark.asyncio
async def test_transient_failure_leaves_the_row_pending():
    db = FakeDb([{"id": "al1", "title": "Bootleg", "artist_id": "ar1"}])
    enr = _enricher(db)

    # The pass ends without raising; nothing is written, so the album is
    # re-selected on the next cycle.
    await enr.run_enrichment()

    assert db.saved == {}
    assert [a["id"] for a in db.pending_albums] == ["al1"]


def test_musicbrainz_no_response_is_transient():
    error = musicbrainzngs.NetworkError(
        cause=urllib.error.URLError("Temporary failure in name resolution")
    )
    with pytest.raises(TransientEnrichmentError):
        raise_musicbrainz_unreachable(error)


def test_musicbrainz_exhausted_rate_limit_is_transient():
    # A NetworkError wrapping an HTTP response is 5xx that survived the
    # library's 8 retries — overload, not a verdict (404s arrive as
    # ResponseError and are recorded as before).
    http_error = urllib.error.HTTPError("url", 503, "busy", hdrs=None, fp=None)
    error = musicbrainzngs.NetworkError("retried 8 times", http_error)
    with pytest.raises(TransientEnrichmentError):
        raise_musicbrainz_unreachable(error)


class _NoClaims:
    """A library that has learned nothing about these entities yet, so every
    lookup falls back to the name on the row."""

    async def get_claims(self, entity_type, entity_id, field):
        return []


def _deezer(tmp_path):
    config = LocalFilesConfig(
        music_folders=[str(tmp_path)],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
    )
    return DeezerPlugin(config, db_manager=_NoClaims())


@pytest.mark.asyncio
async def test_deezer_transport_error_is_transient(tmp_path):
    plugin = _deezer(tmp_path)

    async def refuse(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    plugin.async_client.get = refuse
    with pytest.raises(TransientEnrichmentError):
        await plugin.enrich_artist({"id": "ar1", "name": "VNV Nation"})


@pytest.mark.asyncio
async def test_deezer_http_status_is_a_verdict(tmp_path):
    plugin = _deezer(tmp_path)

    async def not_found(*_a, **_k):
        return httpx.Response(404, request=httpx.Request("GET", "http://x"))

    plugin.async_client.get = not_found
    assert await plugin.enrich_artist({"id": "ar1", "name": "VNV Nation"}) is None


@pytest.mark.parametrize("status", [429, 500, 503])
@pytest.mark.asyncio
async def test_deezer_throttle_and_outage_are_transient(tmp_path, status):
    """A throttle or a 5xx arrives as a response, but it speaks about the
    service, not the artist — recording it would mark the row local-only for
    an outage it knew nothing about."""
    plugin = _deezer(tmp_path)

    async def unavailable(*_a, **_k):
        return httpx.Response(status, request=httpx.Request("GET", "http://x"))

    plugin.async_client.get = unavailable
    with pytest.raises(TransientEnrichmentError):
        await plugin.enrich_artist({"id": "ar1", "name": "VNV Nation"})


@pytest.mark.parametrize("status", [200, 400, 404])
def test_service_verdicts_pass_through(status):
    raise_if_service_unavailable(status, "Deezer")
