"""Tests for AI-search ranking: CLAP KNN scoring + db contract."""

import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.searcher.searcher_db import AsyncSearcherDb
from kalinka_plugin_localfiles.searcher.searcher import SearchWorker


def _make_config(**overrides) -> LocalFilesConfig:
    return LocalFilesConfig(**overrides)


def test_searcher_db_contract():
    """SearchWorker calls these on ``self.db``; removing any one silently
    breaks ai_search at runtime — the handler raises mid-``_do_search`` and the
    plugin returns 0 tracks (no "FROM YOUR LIBRARY" card), which
    unit tests using ``Mock(spec=AsyncSearcherDb)`` do NOT catch.
    """
    required = {
        "knn_search_audio", "knn_search_mood", "get_tracks_va_bulk",
    }
    missing = sorted(m for m in required if not callable(getattr(AsyncSearcherDb, m, None)))
    assert not missing, f"AsyncSearcherDb is missing methods SearchWorker needs: {missing}"


class TestKnnScoring:
    def test_knn_hit_passes_through(self):
        """A perfect neighbour scores 1.0 — the normalised KNN value is the
        score directly (mood blending is applied by the caller)."""
        score = SearchWorker._score_track(knn_norm=1.0, has_knn_hits=True)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_no_knn_hits_returns_zero(self):
        """Edge case: no KNN hits contribute nothing."""
        score = SearchWorker._score_track(knn_norm=0.0, has_knn_hits=False)
        assert score == 0.0
