"""Tests for genre label mapping and query-to-tag matching."""

import pytest

from kalinka_plugin_localfiles.searcher.genre_labels import (
    DISCOGS400_LABELS,
    label_for_index,
)
from kalinka_plugin_localfiles.searcher.query_parser import parse_query


class TestDiscogs400Labels:
    def test_has_400_labels(self):
        assert len(DISCOGS400_LABELS) == 400

    def test_labels_are_formatted_correctly(self):
        for label in DISCOGS400_LABELS:
            assert "---" in label, f"Label missing '---' separator: {label}"

    def test_label_for_index_returns_lowercase(self):
        label = label_for_index(0)
        assert label == "blues---boogie woogie"

    def test_label_for_index_out_of_range(self):
        assert label_for_index(999) == "unknown_999"
        assert label_for_index(-1) == "unknown_-1"

    def test_known_indices(self):
        # Spot-check a few known positions
        assert "blues" in label_for_index(0)
        assert "classical" in label_for_index(20)
        assert "electronic" in label_for_index(35)
        assert "jazz" in label_for_index(207)
        assert "rock" in label_for_index(315)


class TestGenreQueryMatching:
    """Verify that query parser genre keywords match the stored Discogs labels."""

    def _genre_labels_contain(self, keyword: str) -> bool:
        """Check if any Discogs400 label contains the keyword."""
        kw = keyword.lower()
        return any(kw in label.lower() for label in DISCOGS400_LABELS)

    def test_core_genres_match_discogs_labels(self):
        """Top-level genres from query parser must appear in Discogs labels."""
        core_genres = [
            "blues", "classical", "electronic", "folk", "funk",
            "hip hop", "jazz", "latin", "pop", "reggae", "rock", "soul",
        ]
        for genre in core_genres:
            assert self._genre_labels_contain(genre), (
                f"Genre keyword '{genre}' not found in any Discogs400 label"
            )

    def test_subgenres_match_discogs_labels(self):
        """Popular sub-genres from query parser must appear in Discogs labels."""
        subgenres = [
            "ambient", "bluegrass", "bossa nova", "country",
            "disco", "dubstep", "grunge", "heavy metal",
            "house", "opera", "punk", "ska", "techno", "trance",
        ]
        for genre in subgenres:
            assert self._genre_labels_contain(genre), (
                f"Sub-genre keyword '{genre}' not found in any Discogs400 label"
            )

    def test_jazz_query_matches_jazz_label(self):
        """A query for 'jazz' should extract the genre, which matches
        stored labels like 'jazz---cool jazz'."""
        parsed = parse_query("smooth jazz piano")
        # "smooth jazz" is matched as a longer keyword (sorted by length)
        assert "smooth jazz" in parsed.genres

        # Simulate matching against a stored label
        stored_label = label_for_index(226)  # Jazz---Smooth Jazz
        assert "smooth jazz" in stored_label
        # The scoring code checks: if query_genre in genre_labels_string
        assert any(g in stored_label for g in parsed.genres)

    def test_plain_jazz_query(self):
        """A bare 'jazz' query should extract the genre keyword."""
        parsed = parse_query("jazz piano evening")
        assert "jazz" in parsed.genres

    def test_rock_query_matches_rock_labels(self):
        parsed = parse_query("indie rock songs")
        assert "indie rock" in parsed.genres or "rock" in parsed.genres

    def test_electronic_query_extracts_genre(self):
        parsed = parse_query("electronic dance music")
        assert "electronic" in parsed.genres or "dance" in parsed.genres


class TestScoreTrackGenreMatching:
    """End-to-end: query genre keywords match against real Discogs labels."""

    def test_jazz_query_matches_jazz_track(self):
        """Verify the scoring path: query 'jazz' → stored 'jazz---cool jazz'."""
        parsed = parse_query("jazz")
        # Simulate track tags with real Discogs labels
        track_tags = {
            "genres": [
                {"label": label_for_index(212), "score": 0.8},  # Jazz---Cool Jazz
                {"label": label_for_index(205), "score": 0.6},  # Jazz---Afro-Cuban Jazz
            ]
        }
        # This is the matching logic from _score_track
        t_genres = track_tags.get("genres", [])
        genre_labels = " ".join(
            g.get("label", "") for g in t_genres if isinstance(g, dict)
        ).lower()
        matched = sum(1 for qg in parsed.genres if qg in genre_labels)
        genre_score = matched / len(parsed.genres) if parsed.genres else 0.0

        assert genre_score > 0, (
            f"Genre matching failed: query genres={parsed.genres}, "
            f"track labels='{genre_labels}'"
        )

    def test_rock_query_does_not_match_jazz_track(self):
        parsed = parse_query("rock")
        track_tags = {
            "genres": [
                {"label": label_for_index(212), "score": 0.8},  # Jazz---Cool Jazz
            ]
        }
        t_genres = track_tags.get("genres", [])
        genre_labels = " ".join(
            g.get("label", "") for g in t_genres if isinstance(g, dict)
        ).lower()
        matched = sum(1 for qg in parsed.genres if qg in genre_labels)
        assert matched == 0
