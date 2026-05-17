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

    def test_min_margin_rejects_ambiguous_picks(self):
        """When two candidates are within the margin, neither should
        be returned — better to leave the track orphan than commit
        the wrong recording."""
        plugin = _make_mb_plugin()
        candidates = [
            # Two essentially-identical candidates: same title, same MB
            # score, both with perfect duration match. There's no signal
            # to pick between them.
            {"id": "a", "title": "Yesterday", "ext:score": "95", "length": "180000"},
            {"id": "b", "title": "Yesterday", "ext:score": "95", "length": "180000"},
        ]
        best, score, _sim = plugin._find_best_match(
            candidates,
            "Yesterday",
            threshold=70,
            match_key="title",
            target_duration_s=180,
            length_key="length",
            min_margin=5.0,
        )
        assert best is None
        assert score == 0

    def test_min_margin_accepts_clear_winner(self):
        """With a clear margin in either score or duration, the pick
        should still succeed."""
        plugin = _make_mb_plugin()
        candidates = [
            # Clear winner: matches duration; the other has -50 penalty.
            {"id": "winner", "title": "Yesterday", "ext:score": "95", "length": "180000"},
            {"id": "loser", "title": "Yesterday", "ext:score": "95", "length": "320000"},
        ]
        best, _score, _sim = plugin._find_best_match(
            candidates,
            "Yesterday",
            threshold=70,
            match_key="title",
            target_duration_s=180,
            length_key="length",
            min_margin=5.0,
        )
        assert best is not None
        assert best["id"] == "winner"

    def test_min_margin_zero_keeps_old_behavior(self):
        """Default ``min_margin=0`` should not reject anything for
        callers (like artist/album matching) that don't opt in."""
        plugin = _make_mb_plugin()
        candidates = [
            {"id": "a", "title": "Yesterday", "ext:score": "95"},
            {"id": "b", "title": "Yesterday", "ext:score": "95"},
        ]
        best, _score, _sim = plugin._find_best_match(
            candidates,
            "Yesterday",
            threshold=70,
            match_key="title",
        )
        assert best is not None

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
        """Without a target duration, a clearly-higher-scoring result
        still wins."""
        plugin = _make_acoustid_plugin()
        results = [
            {
                "score": 0.95,
                "recordings": [
                    {"id": "winner", "title": "X", "duration": 120, "artists": [], "releases": []},
                ],
            },
            {
                "score": 0.75,
                "recordings": [
                    {"id": "loser", "title": "X", "duration": 360, "artists": [], "releases": []},
                ],
            },
        ]
        match = plugin._get_best_match_info(results, target_duration_s=None)
        assert match is not None
        assert match["recording_mbid"] == "winner"

    def test_ambiguous_top_candidates_returns_none(self):
        """Two candidates with effectively-tied combined scores should
        be held as orphan rather than committed to the wrong release."""
        plugin = _make_acoustid_plugin()
        results = [
            {
                "score": 0.95,
                "recordings": [
                    {"id": "a", "title": "X", "duration": 180, "artists": [], "releases": [{"id": "rel-a", "title": "A"}]},
                ],
            },
            {
                "score": 0.95,
                "recordings": [
                    {"id": "b", "title": "X", "duration": 180, "artists": [], "releases": [{"id": "rel-b", "title": "B"}]},
                ],
            },
        ]
        # Both produce combined = 95 + 20 = 115. Margin = 0 → ambiguous.
        match = plugin._get_best_match_info(results, target_duration_s=180)
        assert match is None

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


class TestAcoustidReleasePicking:
    def test_prefers_album_over_compilation(self):
        """When a recording shows up on both the original studio album
        and a later compilation, we should pick the album."""
        plugin = _make_acoustid_plugin()
        recording = {
            "id": "rec",
            "releasegroups": [
                {
                    "type": "Album",
                    "secondarytypes": ["Compilation"],
                    "releases": [{"id": "comp", "title": "Greatest Hits"}],
                },
                {
                    "type": "Album",
                    "secondarytypes": [],
                    "releases": [{"id": "studio", "title": "Help!"}],
                },
            ],
        }
        title, mbid = plugin._pick_best_release(recording)
        assert mbid == "studio"
        assert title == "Help!"

    def test_prefers_ep_over_live_album(self):
        plugin = _make_acoustid_plugin()
        recording = {
            "releasegroups": [
                {
                    "type": "Album",
                    "secondarytypes": ["Live"],
                    "releases": [{"id": "live", "title": "Live at X"}],
                },
                {
                    "type": "EP",
                    "secondarytypes": [],
                    "releases": [{"id": "ep", "title": "The EP"}],
                },
            ],
        }
        _, mbid = plugin._pick_best_release(recording)
        # Album+Live: 30 - 15 = 15; EP: 20. EP wins.
        assert mbid == "ep"

    def test_falls_back_to_flat_releases(self):
        """When no release-group metadata is present, fall back to
        recording.releases (first one)."""
        plugin = _make_acoustid_plugin()
        recording = {
            "releases": [
                {"id": "a", "title": "A"},
                {"id": "b", "title": "B"},
            ],
        }
        _, mbid = plugin._pick_best_release(recording)
        assert mbid == "a"

    def test_no_releases_returns_none(self):
        plugin = _make_acoustid_plugin()
        title, mbid = plugin._pick_best_release({"id": "rec"})
        assert title is None and mbid is None

    def test_position_match_lifts_correct_release(self):
        """A release whose tracklist places the recording at the local
        file's track number should beat one whose tracklist doesn't —
        useful for picking the original edition over a bonus-track
        reissue when both share a release-group type."""
        plugin = _make_acoustid_plugin()
        recording = {
            "id": "rec-1",
            "releasegroups": [
                {
                    "type": "Album",
                    "secondarytypes": [],
                    "releases": [
                        {
                            "id": "reissue",
                            "title": "Help! (Deluxe)",
                            "mediums": [
                                {
                                    "position": 1,
                                    "tracks": [
                                        {"id": "rec-1", "position": 8},
                                    ],
                                }
                            ],
                        },
                        {
                            "id": "original",
                            "title": "Help!",
                            "mediums": [
                                {
                                    "position": 1,
                                    "tracks": [
                                        {"id": "rec-1", "position": 5},
                                    ],
                                }
                            ],
                        },
                    ],
                }
            ],
        }
        # Local file claims track 5, disc 1.
        _, mbid = plugin._pick_best_release(recording, track_number=5, disc_number=1)
        assert mbid == "original"

    def test_position_match_score_disc_only(self):
        """Disc match alone (without track match) still contributes."""
        release = {
            "mediums": [
                {"position": 2, "tracks": [{"id": "rec", "position": 3}]},
            ]
        }
        # Track 99 doesn't match, but disc 2 does.
        score = AcoustIdPlugin._position_match_score(
            release, "rec", track_number=99, disc_number=2
        )
        assert score == 10.0

    def test_position_match_score_no_data(self):
        """Missing tracklist or missing local position returns 0."""
        assert AcoustIdPlugin._position_match_score({}, "rec", 1, 1) == 0.0
        assert AcoustIdPlugin._position_match_score(
            {"mediums": [{"tracks": [{"id": "rec", "position": 1}]}]},
            "rec",
            None,
            None,
        ) == 0.0

    def test_position_match_score_recording_not_on_release(self):
        """If the recording isn't found in the tracklist, no bonus."""
        release = {
            "mediums": [
                {"position": 1, "tracks": [{"id": "other-rec", "position": 5}]},
            ]
        }
        assert AcoustIdPlugin._position_match_score(release, "rec", 5, 1) == 0.0

    def test_release_group_score_basics(self):
        # Bare 'Album' is the gold standard.
        assert AcoustIdPlugin._release_group_score("Album", []) == 30
        # Compilation pulls Album back below an EP.
        assert AcoustIdPlugin._release_group_score("Album", ["Compilation"]) == 15
        # Soundtrack penalty is harsher.
        assert AcoustIdPlugin._release_group_score("Album", ["Soundtrack"]) == 5
        # Single beats nothing-known.
        assert AcoustIdPlugin._release_group_score("Single", []) == 15
        # Unknown type → 0.
        assert AcoustIdPlugin._release_group_score(None, None) == 0.0
        assert AcoustIdPlugin._release_group_score("", []) == 0.0
