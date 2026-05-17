"""Tests for duration-aware match scoring in the enricher plugins.

Covers:
- The shared duration_bonus / parse_mb_length_seconds helpers.
- MusicBrainz _find_best_match disambiguating same-titled recordings
  when given a target duration.
- AcoustID _get_best_match_info picking the (result, recording) pair
  whose duration matches the local file, not just the first one.
"""

from unittest.mock import MagicMock

import pytest

from kalinka_plugin_localfiles.enricher.match_utils import (
    duration_bonus,
    parse_mb_length_seconds,
)
from kalinka_plugin_localfiles.enricher.acoustid_plugin import AcoustIdPlugin
from kalinka_plugin_localfiles.enricher.musicbrainz_plugin import MusicBrainzPlugin


class TestDurationBonus:
    def test_exact_match(self):
        assert duration_bonus(180, 180) == 20.0

    def test_within_two_seconds(self):
        assert duration_bonus(180, 181.5) == 20.0
        assert duration_bonus(180, 178) == 20.0

    def test_within_five_seconds(self):
        assert duration_bonus(180, 184) == 10.0
        assert duration_bonus(180, 176) == 10.0

    def test_neutral_band(self):
        assert duration_bonus(180, 190) == 0.0
        assert duration_bonus(180, 195) == 0.0

    def test_far_off_penalty(self):
        # Live cut, extended mix, etc.
        assert duration_bonus(180, 360) == -50.0
        assert duration_bonus(180, 60) == -50.0

    @pytest.mark.parametrize("target,cand", [(None, 180), (180, None), (0, 180), (180, 0)])
    def test_missing_returns_zero(self, target, cand):
        assert duration_bonus(target, cand) == 0.0

    def test_unparseable_returns_zero(self):
        assert duration_bonus("not-a-number", 180) == 0.0


class TestParseMbLength:
    def test_string_ms(self):
        assert parse_mb_length_seconds("180000") == 180.0

    def test_int_ms(self):
        assert parse_mb_length_seconds(180500) == 180.5

    def test_none(self):
        assert parse_mb_length_seconds(None) is None

    def test_garbage(self):
        assert parse_mb_length_seconds("abc") is None


def _make_mb_plugin():
    """Build a MusicBrainzPlugin without touching network/config."""
    config = MagicMock()
    config.enricher.plugins.musicbrainz.artist_threshold = 70
    config.enricher.plugins.musicbrainz.album_threshold = 70
    config.enricher.plugins.musicbrainz.track_threshold = 70
    config.enricher.plugins.musicbrainz.string_similarity = 0.6
    config.enricher.plugins.musicbrainz.debug_matching = False
    config.enricher.plugins.user_agent = "test/1.0 (test@example.com)"
    return MusicBrainzPlugin(config, db_manager=MagicMock())


class TestMusicBrainzFindBestMatch:
    def test_duration_picks_correct_recording(self):
        """Two candidates with identical title and MB score, different
        durations — the one matching the local file should win."""
        plugin = _make_mb_plugin()
        # 180s target. Studio version is 180s; live cut is 320s.
        candidates = [
            {"id": "live", "title": "Yesterday", "ext:score": "95", "length": "320000"},
            {"id": "studio", "title": "Yesterday", "ext:score": "95", "length": "180000"},
        ]
        best, score, _sim = plugin._find_best_match(
            candidates,
            "Yesterday",
            threshold=70,
            match_key="title",
            target_duration_s=180,
            length_key="length",
        )
        assert best is not None
        assert best["id"] == "studio"
        assert score == 95

    def test_duration_rescues_lower_title_score(self):
        """A candidate with slightly weaker title but perfect duration
        should beat one with stronger title but wildly wrong duration."""
        plugin = _make_mb_plugin()
        candidates = [
            # ext:score lower, but duration matches exactly
            {"id": "good", "title": "Yesterday", "ext:score": "85", "length": "180000"},
            # ext:score higher but duration is way off (live, 2x length)
            {"id": "bad", "title": "Yesterday", "ext:score": "95", "length": "360000"},
        ]
        best, _score, _sim = plugin._find_best_match(
            candidates,
            "Yesterday",
            threshold=70,
            match_key="title",
            target_duration_s=180,
            length_key="length",
        )
        assert best is not None
        assert best["id"] == "good"

    def test_no_target_duration_preserves_old_behavior(self):
        """When no duration is supplied, scoring reduces to the old
        title+MB-score weighting (higher ext:score wins on tie)."""
        plugin = _make_mb_plugin()
        candidates = [
            {"id": "a", "title": "Yesterday", "ext:score": "80"},
            {"id": "b", "title": "Yesterday", "ext:score": "95"},
        ]
        best, _score, _sim = plugin._find_best_match(
            candidates,
            "Yesterday",
            threshold=70,
            match_key="title",
        )
        assert best is not None
        assert best["id"] == "b"

    def test_missing_length_falls_back_to_neutral(self):
        """A candidate without a length should not be penalized for the
        missing field — it just doesn't get a duration bonus."""
        plugin = _make_mb_plugin()
        candidates = [
            # No length — neutral; ext:score 95.
            {"id": "no_length", "title": "Yesterday", "ext:score": "95"},
            # Wrong duration; ext:score 95 - 50 penalty = ends up lower.
            {"id": "wrong", "title": "Yesterday", "ext:score": "95", "length": "360000"},
        ]
        best, _score, _sim = plugin._find_best_match(
            candidates,
            "Yesterday",
            threshold=70,
            match_key="title",
            target_duration_s=180,
            length_key="length",
        )
        assert best is not None
        assert best["id"] == "no_length"


def _make_acoustid_plugin():
    """Build an AcoustIdPlugin without network."""
    config = MagicMock()
    config.enricher.plugins.acoustid.api_key = "test-key"
    return AcoustIdPlugin(config, db_manager=MagicMock())


class TestAcoustidBestMatch:
    def test_picks_recording_with_matching_duration(self):
        """Top result has two recordings; the one matching duration wins."""
        plugin = _make_acoustid_plugin()
        results = [
            {
                "score": 0.95,
                "recordings": [
                    # Same recording metadata appears repeatedly across
                    # AcoustID responses with different release/duration
                    # contexts; we want the one matching the local file.
                    {
                        "id": "rec-live",
                        "title": "Yesterday",
                        "duration": 320,
                        "artists": [{"id": "a1", "name": "Beatles"}],
                        "releases": [{"id": "rel-live", "title": "Live At Hollywood"}],
                    },
                    {
                        "id": "rec-studio",
                        "title": "Yesterday",
                        "duration": 180,
                        "artists": [{"id": "a1", "name": "Beatles"}],
                        "releases": [{"id": "rel-help", "title": "Help!"}],
                    },
                ],
            }
        ]
        match = plugin._get_best_match_info(results, target_duration_s=180)
        assert match is not None
        assert match["recording_mbid"] == "rec-studio"
        assert match["album_title"] == "Help!"

    def test_picks_across_results(self):
        """Best score isn't necessarily the right recording when duration
        disagrees; a slightly-lower-score result with right duration
        should be preferred."""
        plugin = _make_acoustid_plugin()
        results = [
            {
                "score": 0.92,
                "recordings": [
                    {
                        "id": "rec-wrong-len",
                        "title": "Yesterday",
                        "duration": 320,
                        "artists": [{"id": "a1", "name": "Beatles"}],
                        "releases": [{"id": "rel-live", "title": "Live"}],
                    }
                ],
            },
            {
                "score": 0.80,
                "recordings": [
                    {
                        "id": "rec-right-len",
                        "title": "Yesterday",
                        "duration": 180,
                        "artists": [{"id": "a1", "name": "Beatles"}],
                        "releases": [{"id": "rel-help", "title": "Help!"}],
                    }
                ],
            },
        ]
        # 0.92*100 - 50 = 42  vs  0.80*100 + 20 = 100 → right_len wins.
        match = plugin._get_best_match_info(results, target_duration_s=180)
        assert match is not None
        assert match["recording_mbid"] == "rec-right-len"

    def test_no_target_duration_picks_top_scored(self):
        """Without a target duration, behavior reduces to: pick the
        highest-scored result's first (with-MBID) recording."""
        plugin = _make_acoustid_plugin()
        results = [
            {
                "score": 0.95,
                "recordings": [
                    {"id": "first", "title": "X", "duration": 120, "artists": [], "releases": []},
                    {"id": "second", "title": "X", "duration": 360, "artists": [], "releases": []},
                ],
            }
        ]
        match = plugin._get_best_match_info(results, target_duration_s=None)
        assert match is not None
        # Both recordings get the same base score; we should pick one of them
        # (tie broken by iteration order — first wins).
        assert match["recording_mbid"] == "first"

    def test_skips_recordings_without_mbid(self):
        plugin = _make_acoustid_plugin()
        results = [
            {
                "score": 0.95,
                "recordings": [
                    {"title": "X", "duration": 180},  # no id
                    {"id": "good", "title": "X", "duration": 180, "artists": [], "releases": []},
                ],
            }
        ]
        match = plugin._get_best_match_info(results, target_duration_s=180)
        assert match is not None
        assert match["recording_mbid"] == "good"

    def test_empty_input(self):
        plugin = _make_acoustid_plugin()
        assert plugin._get_best_match_info([], target_duration_s=180) is None
