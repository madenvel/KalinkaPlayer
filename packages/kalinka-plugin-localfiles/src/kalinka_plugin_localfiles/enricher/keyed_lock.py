"""Single-flight helper for the enricher's per-entity caches."""

from __future__ import annotations

import asyncio
from typing import Dict


class KeyedLock:
    """Hands out one ``asyncio.Lock`` per key, created on demand.

    With several entities enriched concurrently, siblings sharing a cache key
    (tracks of one album asking for the same MB release) would all miss the
    cache and all pay the fetch. Holding this lock around the check-fetch-store
    sequence makes the first caller fetch while the rest wait and then hit the
    warm cache.

    Locks are kept for the life of the owning plugin: the key space is the
    albums/releases seen in one enrichment pass, and dropping a lock while a
    waiter still holds a reference would let a later caller take a fresh one
    and defeat the mutual exclusion.
    """

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}

    def __call__(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            # No await between the miss and the store, so concurrent callers
            # on one event loop cannot each install a different lock.
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock
