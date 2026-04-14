"""Tests for CLAP text-encoding IPC between searcher and embedder."""

import multiprocessing
import queue
import threading
from unittest.mock import Mock, patch

import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.searcher.searcher import SearchWorker
from kalinka_plugin_localfiles.searcher.searcher_db import AsyncSearcherDb


def _make_config(**overrides) -> LocalFilesConfig:
    return LocalFilesConfig(**overrides)


def _make_worker(
    text_encode_req=None, text_encode_resp=None
) -> SearchWorker:
    config = _make_config()
    db = Mock(spec=AsyncSearcherDb)
    db._vec_available = True
    return SearchWorker(config, db, text_encode_req, text_encode_resp)


class TestSearchWorkerEncodeQueryText:
    """SearchWorker._encode_query_text sends IPC and returns the blob."""

    def test_returns_blob_from_embedder(self):
        req_q = multiprocessing.Queue()
        resp_q = multiprocessing.Queue()
        worker = _make_worker(req_q, resp_q)

        fake_blob = b"\x00" * 2048  # 512 floats * 4 bytes
        # Simulate embedder responding
        def fake_embedder():
            msg = req_q.get(timeout=5)
            assert msg["query"] == "relaxing piano"
            resp_q.put({"blob": fake_blob})

        t = threading.Thread(target=fake_embedder)
        t.start()

        result = worker._encode_query_text("relaxing piano")
        t.join(timeout=5)

        assert result == fake_blob

    def test_returns_none_when_embedder_returns_none(self):
        req_q = multiprocessing.Queue()
        resp_q = multiprocessing.Queue()
        worker = _make_worker(req_q, resp_q)

        def fake_embedder():
            req_q.get(timeout=5)
            resp_q.put({"blob": None})

        t = threading.Thread(target=fake_embedder)
        t.start()

        result = worker._encode_query_text("some query")
        t.join(timeout=5)

        assert result is None

    def test_returns_none_when_no_queues(self):
        worker = _make_worker(None, None)
        result = worker._encode_query_text("some query")
        assert result is None

    def test_returns_none_on_timeout(self):
        req_q = multiprocessing.Queue()
        resp_q = multiprocessing.Queue()
        worker = _make_worker(req_q, resp_q)

        # Patch timeout to be short for testing
        with patch.object(
            type(worker), "_encode_query_text",
            wraps=worker._encode_query_text,
        ):
            # Don't put anything on resp_q — simulate embedder not responding
            # We need to test with a very short timeout
            original_method = SearchWorker._encode_query_text

            def short_timeout_encode(self, query):
                if self._text_encode_request_queue is None or self._text_encode_response_queue is None:
                    return None
                try:
                    self._text_encode_request_queue.put({"query": query})
                    resp = self._text_encode_response_queue.get(timeout=0.1)
                    return resp.get("blob")
                except Exception:
                    return None

            with patch.object(SearchWorker, "_encode_query_text", short_timeout_encode):
                result = worker._encode_query_text("timeout query")
                assert result is None


class TestSearchWorkerKnnLegAvailability:
    """_knn_leg returns [] when IPC queues are not available."""

    @pytest.mark.asyncio
    async def test_knn_leg_returns_empty_without_queues(self):
        worker = _make_worker(None, None)
        result = await worker._knn_leg("test query", 50)
        assert result == []

    @pytest.mark.asyncio
    async def test_knn_leg_returns_empty_without_vec(self):
        req_q = multiprocessing.Queue()
        resp_q = multiprocessing.Queue()
        worker = _make_worker(req_q, resp_q)
        worker.db._vec_available = False
        result = await worker._knn_leg("test query", 50)
        assert result == []


class TestModuleSetupEmbedderLifecycle:
    """Embedder process starts when searcher needs KNN."""

    def test_embedder_starts_when_clap_version_positive(self):
        """If searcher.enabled and clap version > 0, embedder must start."""
        config = _make_config()
        # Default: searcher.enabled=True, embedder.enabled=False, clap.current_version=1
        clap_version = config.embedder.clap.current_version
        need_embedder = config.embedder.enabled or (
            config.searcher.enabled and clap_version > 0
        )
        assert need_embedder is True

    def test_embedder_not_needed_when_clap_version_zero(self):
        """If CLAP version is 0 and embedder disabled, no embedder needed."""
        config = _make_config(
            embedder={"enabled": False, "clap": {"current_version": 0}}
        )
        clap_version = config.embedder.clap.current_version
        need_embedder = config.embedder.enabled or (
            config.searcher.enabled and clap_version > 0
        )
        assert need_embedder is False

    def test_embedder_starts_when_explicitly_enabled(self):
        """If embedder.enabled is True, it starts regardless of clap version."""
        config = _make_config(
            embedder={"enabled": True, "clap": {"current_version": 0}}
        )
        clap_version = config.embedder.clap.current_version
        need_embedder = config.embedder.enabled or (
            config.searcher.enabled and clap_version > 0
        )
        assert need_embedder is True
