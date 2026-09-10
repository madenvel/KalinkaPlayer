#!/usr/bin/env python3
"""AcoustID as the identity rescue, and only that.

Reading the audio is the expensive answer, so it is asked when the cheap ones
failed: no external source identified the track, or the only thing naming it
is its own path — and a filename is what the text search was built from, so a
match on one proves nothing the audio cannot overturn. A missing *album* never
triggers a lookup; album identity is the clustering pass's job. Its writes stay
fill-only: it records the recording mbid but never overwrites a tagged title or
repoints a known artist.
"""

from unittest.mock import patch

import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.enricher.acoustid_plugin import AcoustIdPlugin


class _FakeDb:
    """Minimal db_manager stand-in, with settable provenance."""

    def __init__(self, origins=None):
        self.origins = origins or {}

    async def get_resolved_origin(self, entity_type, entity_id, field):
        return self.origins.get((entity_type, entity_id, field))

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


def _plugin(origins=None) -> AcoustIdPlugin:
    cfg = LocalFilesConfig(db_path=":memory:")
    cfg.enricher.plugins.acoustid.api_key = "test-key"
    return AcoustIdPlugin(cfg, _FakeDb(origins))


def _guess(entity_type, entity_id, field):
    return {(entity_type, entity_id, field): {"source": "filename",
                                              "tier": "guessed"}}


def _identified(**over):
    track = {
        "id": "t1",
        "file_path": "/m/t1.flac",
        "artist_id": "ar1",
        "title": "Real Song",
        "album_id": "al1",
        "mbid": "rec-mbid",
        "enriched": 0,
    }
    track.update(over)
    return track


# --- gating -----------------------------------------------------------------


def test_version_bumped_for_the_provenance_gate():
    # Bumping ENRICHER_VERSION re-opens previously-FAILED tracks so the new
    # gate is re-evaluated (fingerprint-gated retry).
    assert AcoustIdPlugin.ENRICHER_VERSION >= 3


@pytest.mark.asyncio
async def test_unresolved_helpers():
    plugin = _plugin()
    assert await plugin._artist_is_unresolved({"artist_id": "unknown_artist"})
    assert await plugin._artist_is_unresolved({"artist_id": None})
    assert await plugin._artist_is_unresolved({})
    assert not await plugin._artist_is_unresolved({"artist_id": "ar1"})

    assert await plugin._title_is_unresolved({"id": "t1", "title": ""})
    assert await plugin._title_is_unresolved({"id": "t1", "title": "   "})
    assert not await plugin._title_is_unresolved({"id": "t1", "title": "Song"})

    # A row with no recorded origin predates provenance and counts as named.
    guessed = _plugin(_guess("track", "t1", "title"))
    assert await guessed._title_is_unresolved({"id": "t1", "title": "Song"})


@pytest.mark.asyncio
async def test_missing_album_alone_does_not_trigger_lookup():
    """A fingerprint is never spent on album membership: an identified,
    tagged track stuck in unknown_album is not read."""
    plugin = _plugin()
    with patch.object(plugin, "_generate_fingerprint") as gen:
        result = await plugin.enrich_track(_identified(album_id="unknown_album"))
    assert result is None
    gen.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "track,origins",
    [
        (_identified(artist_id="unknown_artist"), None),
        (_identified(title=""), None),
        # Identified, named — but only by its own path, and the text search
        # that produced that mbid was built from exactly that name.
        (_identified(), _guess("track", "t1", "title")),
        (_identified(), _guess("artist", "ar1", "name")),
        # Nothing identified it at all.
        (_identified(mbid=None), None),
    ],
    ids=["no artist", "no title", "guessed title", "guessed artist", "no mbid"],
)
async def test_a_track_that_is_not_both_identified_and_named_is_read(
    track, origins
):
    plugin = _plugin(origins)
    with patch.object(
        plugin, "_generate_fingerprint", return_value=(None, None)
    ) as gen:
        await plugin.enrich_track(track)
    gen.assert_called_once()


@pytest.mark.asyncio
async def test_an_identified_and_tagged_track_is_left_alone():
    plugin = _plugin()
    with patch.object(plugin, "_generate_fingerprint") as gen:
        assert await plugin.enrich_track(_identified()) is None
    gen.assert_not_called()


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
    result = await _run_with_match(plugin, _identified(title=""))
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
async def test_a_rescue_claim_is_verified_not_inferred():
    """Only a direct identifier may replace a display value outright, and a
    fingerprint is one — otherwise it could not overturn the text match that
    was built from the same guess it is correcting."""
    plugin = _plugin(_guess("track", "t1", "title"))
    result = await _run_with_match(plugin, _identified(title="01 Track"))
    assert [c["tier"] for c in result["claims"] if c["field"] == "title"] == [
        "verified"
    ]


@pytest.mark.asyncio
async def test_a_tagged_title_yields_no_claim_at_all():
    plugin = _plugin()
    result = await _run_with_match(
        plugin, _identified(artist_id="unknown_artist", title="Local Title")
    )
    updates = result["updates"]
    assert updates["mbid"] == "rec-mbid"
    # Present locally and not a guess → uncontested, so nothing is claimed.
    assert "title" not in updates
    assert not [c for c in result["claims"] if c["field"] == "title"]
    # Artist was absent → filled.
    assert updates["artist_name"] == "MB Artist"
