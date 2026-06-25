"""Mood / semantic search index for Jamendo (text-only).

Backs `JamendoInputModule.ai_search`: a natural-language query is embedded with
MiniLM and matched (cosine KNN, sqlite-vec) against precomputed embeddings of
the JamendoMaxCaps captions — one vector per Jamendo track. The index is built
offline (kalinka-training `jamendomaxcaps_embed.py`) and shipped as an asset;
this class just opens it read-only and answers KNN queries.

Returns (track_id, distance) only — the plugin resolves track metadata via the
Jamendo API. Everything degrades gracefully: if the model, the index, or
sqlite-vec is missing, `available()` is False and ai_search returns nothing.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List, Optional, Tuple

from .minilm_onnx import MiniLmOnnx, ensure_model

logger = logging.getLogger(__name__.split(".")[-1])


class JamendoMoodIndex:
    def __init__(
        self,
        index_path: str,
        model_dir: str,
        model_base_url: Optional[str] = None,
    ):
        self._index_path = os.path.expanduser(index_path)
        self._model_dir = os.path.expanduser(model_dir)
        self._model_base_url = model_base_url
        self._encoder = MiniLmOnnx(self._model_dir)
        self._available: Optional[bool] = None
        self._lock = asyncio.Lock()  # serialize encode+query (one model, low QPS)

    async def available(self) -> bool:
        """True if the index db, sqlite-vec, and the model are all usable."""
        if self._available is not None:
            return self._available
        ok = True
        if not os.path.exists(self._index_path):
            logger.info("Jamendo mood index not found at %s", self._index_path)
            ok = False
        if ok:
            try:
                import sqlite_vec  # noqa: F401
            except Exception as e:
                logger.warning("sqlite-vec unavailable (%s); ai_search disabled", e)
                ok = False
        if ok and not ensure_model(self._model_dir, self._model_base_url):
            logger.warning(
                "MiniLM model missing in %s and not downloadable; ai_search disabled",
                self._model_dir,
            )
            ok = False
        self._available = ok
        if ok:
            logger.info("Jamendo mood index ready (%s)", self._index_path)
        return ok

    async def search(self, query: str, limit: int) -> List[Tuple[int, float]]:
        """Return [(track_id, distance)] nearest to the query, ascending."""
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
        """CPU-bound: encode the query to float32 bytes (runs in executor)."""
        return self._encoder.encode_one(query).tobytes()

    async def _knn(self, blob: bytes, limit: int) -> List[Tuple[int, float]]:
        import aiosqlite
        import sqlite_vec

        try:
            async with aiosqlite.connect(
                f"file:{self._index_path}?mode=ro", uri=True, timeout=5.0
            ) as conn:
                await conn.enable_load_extension(True)
                await conn.load_extension(sqlite_vec.loadable_path())
                await conn.enable_load_extension(False)
                cur = await conn.execute(
                    "SELECT track_id, distance FROM vec_tracks"
                    " WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                    (blob, limit),
                )
                rows = await cur.fetchall()
            return [(int(r[0]), float(r[1])) for r in rows]
        except Exception as e:
            logger.warning("Jamendo KNN query failed: %s", e)
            return []
