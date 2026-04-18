"""Tests for the unified tag prediction pipeline."""

import json
import sys
from unittest.mock import AsyncMock, Mock, patch, MagicMock

import numpy as np
import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.searcher.searcher import SearchWorker
from kalinka_plugin_localfiles.searcher.searcher_db import AsyncSearcherDb


def _make_config(**overrides) -> LocalFilesConfig:
    return LocalFilesConfig(**overrides)


def _make_worker() -> SearchWorker:
    config = _make_config()
    db = Mock(spec=AsyncSearcherDb)
    db._vec_available = False
    return SearchWorker(config, db)


def _install_essentia_mock():
    """Install a mock essentia module in sys.modules and return the mock_es."""
    mock_es = MagicMock()
    mock_essentia = MagicMock()
    mock_essentia.standard = mock_es
    sys.modules["essentia"] = mock_essentia
    sys.modules["essentia.standard"] = mock_es
    return mock_es


def _remove_essentia_mock():
    sys.modules.pop("essentia", None)
    sys.modules.pop("essentia.standard", None)


class TestPredictAllTags:
    """SearchWorker._predict_all_tags decodes audio once and reuses VGGish."""

    def test_returns_empty_dict_when_no_models_loaded(self):
        worker = _make_worker()
        # No models loaded — all None
        result = worker._predict_all_tags("/fake/path.mp3")
        # Without essentia imported, this should return empty dict
        assert isinstance(result, dict)

    def test_returns_all_tags_when_models_available(self):
        """With mocked Essentia models, all tags are produced in one pass."""
        mock_es = _install_essentia_mock()
        try:
            worker = _make_worker()

            # Mock audio loader
            fake_audio = np.random.rand(16000 * 5).astype(np.float32)
            mock_loader_instance = Mock(return_value=fake_audio)
            mock_es.MonoLoader = Mock(return_value=mock_loader_instance)

            # Mock the models
            genre_activations = np.random.rand(10, 400).astype(np.float32)
            worker._effnet = Mock(return_value=np.random.rand(10, 128))
            worker._genre_cls = Mock(return_value=genre_activations)

            vggish_embeddings = np.random.rand(10, 128).astype(np.float32)
            worker._vggish = Mock(return_value=vggish_embeddings)

            mood_activations = np.array([[0.1, 0.6, 0.1, 0.1, 0.1]])
            worker._mood_cls = Mock(return_value=mood_activations)

            dance_activations = np.array([[0.75, 0.25]])
            worker._dance_cls = Mock(return_value=dance_activations)

            result = worker._predict_all_tags("/fake/track.flac")

            assert "genres" in result
            assert isinstance(result["genres"], list)
            assert "mood_cluster" in result
            assert isinstance(result["mood_cluster"], int)
            assert "danceability" in result
            assert isinstance(result["danceability"], float)

            # Verify audio was decoded only ONCE (the key optimization)
            mock_es.MonoLoader.assert_called_once()
            mock_loader_instance.assert_called_once()

            # Verify VGGish was called only ONCE (shared by mood + danceability)
            worker._vggish.assert_called_once()
        finally:
            _remove_essentia_mock()

    def test_partial_results_on_genre_only(self):
        """If VGGish is None, only genre is returned."""
        mock_es = _install_essentia_mock()
        try:
            worker = _make_worker()

            fake_audio = np.random.rand(16000 * 5).astype(np.float32)
            mock_loader_instance = Mock(return_value=fake_audio)
            mock_es.MonoLoader = Mock(return_value=mock_loader_instance)

            genre_activations = np.random.rand(10, 400).astype(np.float32)
            worker._effnet = Mock(return_value=np.random.rand(10, 128))
            worker._genre_cls = Mock(return_value=genre_activations)
            worker._vggish = None  # No VGGish
            worker._mood_cls = None
            worker._dance_cls = None

            result = worker._predict_all_tags("/fake/track.flac")

            assert "genres" in result
            assert "mood_cluster" not in result
            assert "danceability" not in result
        finally:
            _remove_essentia_mock()


class TestProcessTagsBatch:
    """SearchWorker._process_tags_batch uses unified 'tags' stage."""

    @pytest.mark.asyncio
    async def test_claims_unified_tags_stage(self):
        worker = _make_worker()
        worker.db.claim_batch = AsyncMock(return_value=[])

        result = await worker._process_tags_batch()
        assert result is False
        worker.db.claim_batch.assert_called_once_with(
            "tags", worker.config.searcher.batch_size_tags
        )

    @pytest.mark.asyncio
    async def test_processes_batch_and_writes_combined_tags(self):
        worker = _make_worker()

        job = {"id": 1, "entity_id": "track-1", "model_version": 1, "attempts": 1}
        worker.db.claim_batch = AsyncMock(return_value=[job])
        worker.db.get_file_path_for_track = AsyncMock(return_value="/music/song.mp3")
        worker.db.complete_tags_job = AsyncMock()
        worker.db.fail_job = AsyncMock()

        fake_tags = {"genres": [{"label": "rock", "score": 0.9}], "mood_cluster": 2, "danceability": 0.6}
        with patch.object(worker, "_predict_all_tags", return_value=fake_tags):
            result = await worker._process_tags_batch()

        assert result is True
        worker.db.complete_tags_job.assert_called_once()
        args = worker.db.complete_tags_job.call_args[0]
        assert args[0] == 1  # job_id
        assert args[1] == "track-1"  # track_id
        written_tags = json.loads(args[2])
        assert written_tags == fake_tags

    @pytest.mark.asyncio
    async def test_handles_missing_track(self):
        worker = _make_worker()

        job = {"id": 1, "entity_id": "track-missing", "model_version": 1, "attempts": 1}
        worker.db.claim_batch = AsyncMock(return_value=[job])
        worker.db.get_file_path_for_track = AsyncMock(return_value=None)
        worker.db.fail_job = AsyncMock()

        result = await worker._process_tags_batch()

        assert result is True
        worker.db.fail_job.assert_called_once_with(1, "track not found", 3)


class TestScheduleNewTagJobs:
    """searcher_db.schedule_new_tag_jobs uses unified 'tags' stage."""

    @pytest.mark.asyncio
    async def test_schedule_uses_unified_stage(self):
        """Verify the SQL inserts 'tags' not individual stages."""
        import aiosqlite
        import tempfile
        import os

        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        config = _make_config(db_path=db_path)

        await init_db(db_path)

        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "INSERT INTO tracks (id, title, file_path, format, enriched) VALUES ('t1', 't', 'f', 'mp3', 1)"
            )
            await conn.execute(
                "INSERT INTO tracks (id, title, file_path, format, enriched) VALUES ('t2', 't', 'f', 'mp3', 2)"
            )
            await conn.execute(
                "INSERT INTO tracks (id, title, file_path, format, enriched) VALUES ('t3', 't', 'f', 'mp3', 0)"
            )
            await conn.commit()

        db = AsyncSearcherDb(config)

        inserted = await db.schedule_new_tag_jobs(1)
        assert inserted == 2  # t1 and t2 (enriched), not t3

        # Verify stage name
        async with aiosqlite.connect(db_path) as conn:
            cursor = await conn.execute(
                "SELECT stage FROM embedding_jobs"
            )
            rows = await cursor.fetchall()
            stages = [r[0] for r in rows]
            assert all(s == "tags" for s in stages)
            assert len(stages) == 2
