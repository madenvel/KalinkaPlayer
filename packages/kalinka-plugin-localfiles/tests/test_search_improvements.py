"""Tests for AI-search ranking: dynamic re-rank weight normalisation."""

import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.searcher.searcher_db import AsyncSearcherDb
from kalinka_plugin_localfiles.searcher.searcher import SearchWorker
from kalinka_plugin_localfiles.searcher.query_parser import parse_query


def _make_config(**overrides) -> LocalFilesConfig:
    return LocalFilesConfig(**overrides)


class TestDynamicWeightNormalization:
    def _make_worker(self):
        from unittest.mock import Mock
        config = _make_config()
        db = Mock(spec=AsyncSearcherDb)
        db._vec_available = False
        return SearchWorker(config, db)

    def test_knn_only_uses_full_range(self):
        """With only KNN hits and no tags, a perfect neighbour scores 1.0 —
        the single active weight normalises to the full 0-1 range."""
        worker = self._make_worker()
        parsed = parse_query("beatles")  # no tag constraints

        score = worker._score_track(
            parsed, knn_norm=1.0, track_tags=None, has_knn_hits=True,
        )
        assert score == pytest.approx(1.0, abs=0.01)

    def test_knn_and_tags_normalise_together(self):
        """Tag components blend with KNN; both perfect -> 1.0 after the
        dynamic weight normalisation."""
        worker = self._make_worker()
        parsed = parse_query("jazz piano")  # has genre constraint

        track_tags = {
            "genres": [{"label": "jazz---cool jazz", "score": 0.8}],
        }

        score = worker._score_track(
            parsed, knn_norm=1.0, track_tags=track_tags, has_knn_hits=True,
        )
        # (weight_knn*1 + weight_genre*1) / (weight_knn + weight_genre) = 1.0
        assert score == pytest.approx(1.0, abs=0.01)

    def test_no_active_inputs_returns_zero(self):
        """Edge case: no KNN hits and no tag constraints."""
        worker = self._make_worker()
        parsed = parse_query("something")

        score = worker._score_track(
            parsed, knn_norm=0.0, track_tags=None, has_knn_hits=False,
        )
        assert score == 0.0
