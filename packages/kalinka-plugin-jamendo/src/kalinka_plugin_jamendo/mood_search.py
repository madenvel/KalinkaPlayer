"""Mood/semantic search index for the Jamendo plugin.

Embeds the query with MiniLM and KNN-matches it (cosine, sqlite-vec) against
precomputed MiniLM embeddings of the JamendoMaxCaps captions, one vector per
track. The index + model are downloaded assets; this opens the index read-only
and returns (track_id, distance). If an asset is missing and can't be fetched,
``available()`` is False and ai_search returns nothing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from typing import List, Optional, Tuple

from .minilm_onnx import MiniLmOnnx, download_file, ensure_model

logger = logging.getLogger(__name__.split(".")[-1])


class JamendoMoodIndex:
    def __init__(self, index_path: str, index_url: Optional[str],
                 model_dir: str, model_url: Optional[str]):
        self._index_path = os.path.expanduser(index_path)
        self._index_url = index_url
        self._model_dir = os.path.expanduser(model_dir)
        self._model_url = model_url
        self._encoder = MiniLmOnnx(self._model_dir)
        self._lock = asyncio.Lock()  # one model, low QPS — serialize queries
        self._available: Optional[bool] = None
        self._dtype = "float32"
        self._int8_scale = 508.0

    async def available(self) -> bool:
        if self._available is None:
            loop = asyncio.get_running_loop()
            self._available = await loop.run_in_executor(None, self._provision)
        return self._available

    def _provision(self) -> bool:
        """Fetch the index + model if missing; read the index dtype/scale.

        Blocking (downloads, sqlite) — always called via an executor.
        """
        try:
            import sqlite_vec  # noqa: F401
        except Exception as e:
            logger.warning("sqlite-vec unavailable (%s); ai_search off", e)
            return False
        if not os.path.exists(self._index_path) and not (
            self._index_url and download_file(self._index_url, self._index_path)
        ):
            logger.warning("Jamendo index missing (%s); ai_search off",
                           self._index_path)
            return False
        if not ensure_model(self._model_dir, self._model_url):
            logger.warning("MiniLM model missing (%s); ai_search off",
                           self._model_dir)
            return False
        meta = self._read_meta()
        self._dtype = meta.get("dtype", "float32")
        self._int8_scale = float(meta.get("int8_scale", 508.0))
        logger.info("Jamendo mood index ready (%s, %s)",
                    self._index_path, self._dtype)
        return True

    def _read_meta(self) -> dict:
        con = sqlite3.connect(f"file:{self._index_path}?mode=ro", uri=True)
        try:
            return dict(con.execute("SELECT key, value FROM meta").fetchall())
        finally:
            con.close()

    async def search(self, query: str, limit: int) -> List[Tuple[int, float]]:
        if not await self.available():
            return []
        async with self._lock:
            loop = asyncio.get_running_loop()
            try:
                blob = await loop.run_in_executor(None, self._encode, query)
            except Exception as e:
                logger.warning("query encode failed: %s", e)
                return []
            return await self._knn(blob, limit)

    def _encode(self, query: str) -> bytes:
        """Encode the query, quantized to int8 when the index is int8."""
        vec = self._encoder.encode_one(query)  # float32 (384,), L2-normalized
        if self._dtype != "int8":
            return vec.tobytes()
        import numpy as np
        return np.clip(np.round(vec * self._int8_scale), -127, 127).astype(
            np.int8).tobytes()

    async def _knn(self, blob: bytes, limit: int) -> List[Tuple[int, float]]:
        import aiosqlite
        import sqlite_vec

        match = "vec_int8(?)" if self._dtype == "int8" else "?"
        try:
            async with aiosqlite.connect(
                f"file:{self._index_path}?mode=ro", uri=True, timeout=5.0
            ) as conn:
                await conn.enable_load_extension(True)
                await conn.load_extension(sqlite_vec.loadable_path())
                await conn.enable_load_extension(False)
                cur = await conn.execute(
                    "SELECT track_id, distance FROM vec_tracks"
                    f" WHERE embedding MATCH {match} ORDER BY distance LIMIT ?",
                    (blob, limit),
                )
                rows = await cur.fetchall()
            return [(int(r[0]), float(r[1])) for r in rows]
        except Exception as e:
            logger.warning("Jamendo KNN query failed: %s", e)
            return []
