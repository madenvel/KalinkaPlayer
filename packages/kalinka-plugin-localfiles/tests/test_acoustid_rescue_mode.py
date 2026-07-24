#!/usr/bin/env python3
"""Phase 2f: AcoustID narrowed to identity rescue mode.

AcoustID's only job is "what track is this?" when local evidence couldn't
answer. It fires solely when the artist or title is still absent after every
local source — a missing *album* no longer triggers a lookup (album identity
is the clustering pass's job) — and its writes are fill-only: it records the
recording mbid but never overwrites a local title or repoints a known artist.
"""

from unittest.mock import patch

import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.enricher.acoustid_plugin import AcoustIdPlugin


class _FakeDb:
    """Minimal db_manager stand-in — rescue-mode tests never reach it."""

    async def save_fingerprint(self, *a, **k):
        return None

    async def get_album_by_id(self, *a, **k):
        return None

    async def get_artist_by_mbid(self, *a, **k):
        return None

    async def search_artists(self, *a, **k):
        return [], 0

    async def insert_artist(self, *a, **k):
        return None


def _plugin() -> AcoustIdPlugin:
    cfg = LocalFilesConfig(db_path=":memory:")
    cfg.enricher.plugins.acoustid.api_key = "test-key"
    return AcoustIdPlugin(cfg, _FakeDb())


# --- gating -----------------------------------------------------------------


def test_version_bumped_for_rescue_mode():
    # Bumping ENRICHER_VERSION re-opens previously-FAILED tracks so the new
    # gate is re-evaluated (fingerprint-gated retry).
    assert AcoustIdPlugin.ENRICHER_VERSION >= 2


def test_absence_helpers():
    assert AcoustIdPlugin._artist_absent({"artist_id": "unknown_artist"})
    assert AcoustIdPlugin._artist_absent({"artist_id": None})
    assert AcoustIdPlugin._artist_absent({})
    assert not AcoustIdPlugin._artist_absent({"artist_id": "ar1"})

    assert AcoustIdPlugin._title_absent({"title": ""})
    assert AcoustIdPlugin._title_absent({"title": "   "})
    assert AcoustIdPlugin._title_absent({})
    assert not AcoustIdPlugin._title_absent({"title": "Song"})


@pytest.mark.asyncio
async def test_missing_album_alone_does_not_trigger_lookup():
    """The key 2f change: a track with a known artist + title but stuck in
    unknown_album is NOT fingerprinted — no fpcalc, no web request."""
    plugin = _plugin()
    track = {
        "id": "t1",
        "file_path": "/m/t1.flac",
        "artist_id": "ar1",
        "title": "Real Song",
        "album_id": "unknown_album",
        "enriched": 0,
    }
    with patch.object(plugin, "_generate_fingerprint") as gen:
        result = await plugin.enrich_track(track)
    assert result is None
    gen.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_artist_triggers_lookup():
    plugin = _plugin()
    track = {
        "id": "t1",
        "file_path": "/m/t1.flac",
        "artist_id": "unknown_artist",
        "title": "Some Title",
        "album_id": "al1",
        "enriched": 0,
    }
    with patch.object(
        plugin, "_generate_fingerprint", return_value=(None, None)
    ) as gen:
        await plugin.enrich_track(track)
    gen.assert_called_once()


@pytest.mark.asyncio
async def test_absent_title_triggers_lookup():
    plugin = _plugin()
    track = {
        "id": "t1",
        "file_path": "/m/t1.flac",
        "artist_id": "ar1",
        "title": "",
        "album_id": "al1",
        "enriched": 0,
    }
    with patch.object(
        plugin, "_generate_fingerprint", return_value=(None, None)
    ) as gen:
        await plugin.enrich_track(track)
    gen.assert_called_once()


# --- fill-only writes -------------------------------------------------------


_MATCH = {
    "score": 0.95,
    "recording_mbid": "rec-mbid",
    "title": "MB Title",
    "artist_name": "MB Artist",
    "artist_mbid": "art-mbid",
    "album_title": None,
    "album_mbid": None,
}


async def _run_with_match(plugin, track):
    with patch.object(
        plugin, "_generate_fingerprint", return_value=("fp", 200)
    ), patch.object(
        plugin, "_lookup_fingerprint", return_value=[{"x": 1}]
    ), patch.object(
        plugin, "_get_best_match_info", return_value=dict(_MATCH)
    ):
        return await plugin.enrich_track(track)


@pytest.mark.asyncio
async def test_absent_title_is_filled_from_match():
    plugin = _plugin()
    track = {
        "id": "t1",
        "file_path": "/m/t1.flac",
        "artist_id": "ar1",  # known artist
        "title": "",  # absent title → the rescue field
        "album_id": "al1",
        "enriched": 0,
    }
    result = await _run_with_match(plugin, track)
    updates = result["updates"]
    assert updates["mbid"] == "rec-mbid"
    # Title is a resolvable field: emitted as a claim, not a direct write —
    # resolution is the sole writer.
    assert "title" not in updates
    assert ("title", "MB Title") in {
        (c["field"], c["value"]) for c in result["claims"]
    }
    # Known artist must not be repointed.
    assert "artist_id" not in updates


@pytest.mark.asyncio
async def test_present_title_is_not_overwritten():
    plugin = _plugin()
    track = {
        "id": "t1",
        "file_path": "/m/t1.flac",
        "artist_id": "unknown_artist",  # this is the rescue field
        "title": "Local Title",  # present → must survive
        "album_id": "al1",
        "enriched": 0,
    }
    result = await _run_with_match(plugin, track)
    updates = result["updates"]
    assert updates["mbid"] == "rec-mbid"
    # Title is present locally → not overwritten.
    assert "title" not in updates
    # Artist was absent → filled.
    assert updates["artist_name"] == "MB Artist"
