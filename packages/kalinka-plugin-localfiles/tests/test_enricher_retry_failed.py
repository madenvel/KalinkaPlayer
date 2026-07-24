"""Tests for the unconditional retry-reset primitive.

Background: a row that can still improve when the enrichment setup changes
must be re-openable. Two populations qualify: ``enriched=2`` (FAILED — a
required local field never resolved) and, since Phase 2e, ``enriched=1`` with
no ``mbid`` (ENRICHED *local-only* — local identity resolved but no external
match). ``AsyncEnricherDb.reset_failed_to_retry`` flips both back to ``0`` so
a new matcher tier / bug fix re-attempts them; a fully-matched row (mbid set)
is left alone so display never churns.

The enricher worker doesn't call this directly at startup anymore — it
calls the *gated* ``reset_failed_for_fingerprint`` so an unchanged
setup doesn't re-hammer the providers (see
``test_enricher_fingerprint_retry.py``). This primitive is still the
shared building block and is tested here in isolation.
"""

from __future__ import annotations

import aiosqlite
import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.enricher.enricher_db import AsyncEnricherDb


def _config(tmp_path) -> LocalFilesConfig:
    """Build a config with both db_path and artwork_path under tmp_path
    — the default config points artwork at ``/var/cache/kalinka`` which
    is not writable in a CI/test env."""
    return LocalFilesConfig(
        db_path=str(tmp_path / "test.db"),
        artwork_path=str(tmp_path / "artwork"),
    )


async def _seed(db_path: str) -> None:
    """Insert a mix of statuses across all three tables. Since Phase 2e the
    reset re-opens FAILED rows *and* local-only ENRICHED rows (enriched=1 with
    no mbid); a fully-matched ENRICHED row (mbid set) must be left alone. So
    each table gets a matched-done row (mbid) and a local-only row (no mbid)."""
    async with aiosqlite.connect(db_path) as conn:
        # Artists: pending, matched-done (mbid), local-only (no mbid), 2 failed.
        await conn.executemany(
            "INSERT INTO artists (id, name, enriched, mbid) VALUES (?, ?, ?, ?)",
            [
                ("ar_pending", "Pending", 0, None),
                ("ar_matched", "Matched", 1, "mbid-ar"),
                ("ar_local", "Local Only", 1, None),
                ("ar_failed1", "Failed One", 2, None),
                ("ar_failed2", "Failed Two", 2, None),
            ],
        )
        # Albums: matched-done (mbid), local-only (no mbid), 3 failed.
        await conn.executemany(
            "INSERT INTO albums (id, title, artist_id, enriched, mbid) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("al_matched", "Matched", "ar_matched", 1, "mbid-al"),
                ("al_local", "Local Only", "ar_local", 1, None),
                ("al_failed1", "Failed One", "ar_failed1", 2, None),
                ("al_failed2", "Failed Two", "ar_failed2", 2, None),
                ("al_failed3", "Failed Three", "ar_failed1", 2, None),
            ],
        )
        # Tracks: 2 matched (mbid), 1 local-only (no mbid), 5 failed, 1 pending.
        await conn.executemany(
            "INSERT INTO tracks (id, title, file_path, format, enriched, mbid) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("t_matched1", "T1", "/f1", "mp3", 1, "mbid-t1"),
                ("t_matched2", "T2", "/f2", "mp3", 1, "mbid-t2"),
                ("t_local", "Tl", "/fl", "mp3", 1, None),
                ("t_pending", "Tp", "/fp", "mp3", 0, None),
                ("t_failed1", "Tf1", "/ff1", "mp3", 2, None),
                ("t_failed2", "Tf2", "/ff2", "mp3", 2, None),
                ("t_failed3", "Tf3", "/ff3", "mp3", 2, None),
                ("t_failed4", "Tf4", "/ff4", "mp3", 2, None),
                ("t_failed5", "Tf5", "/ff5", "mp3", 2, None),
            ],
        )
        await conn.commit()


async def _count_by_status(db_path: str):
    out = {}
    async with aiosqlite.connect(db_path) as conn:
        for table in ("artists", "albums", "tracks"):
            cur = await conn.execute(
                f"SELECT enriched, COUNT(*) FROM {table} GROUP BY enriched"
            )
            out[table] = dict(await cur.fetchall())
    return out


@pytest.mark.asyncio
async def test_reset_reopens_failed_and_local_only(tmp_path):
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    await _seed(cfg.db_path)
    db = AsyncEnricherDb(cfg)

    before = await _count_by_status(cfg.db_path)
    # Sanity: fixture has the expected starting distribution.
    assert before["artists"].get(2) == 2
    assert before["albums"].get(2) == 3
    assert before["tracks"].get(2) == 5

    counts = await db.reset_failed_to_retry()
    # FAILED (2/3/5) + local-only ENRICHED with no mbid (1/1/1).
    assert counts == {"artists": 3, "albums": 4, "tracks": 6}

    after = await _count_by_status(cfg.db_path)
    # No FAILED rows remain anywhere.
    for table in ("artists", "albums", "tracks"):
        assert 2 not in after[table], f"{table} still has FAILED rows: {after[table]}"
    # Only the fully-matched (mbid) ENRICHED rows survive at enriched=1.
    assert after["artists"].get(1) == 1   # ar_matched
    assert after["albums"].get(1) == 1    # al_matched
    assert after["tracks"].get(1) == 2    # t_matched1/2
    # 1 pending + 5 failed + 1 local-only, all reset to 0.
    assert after["tracks"].get(0) == 1 + 5 + 1


@pytest.mark.asyncio
async def test_reset_is_safe_on_db_with_no_failed_rows(tmp_path):
    """A second call within the same process is a no-op — the
    once-per-restart contract is enforced by the worker, not the DB
    method itself, but the method has to behave when there's nothing
    to reset."""
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    await _seed(cfg.db_path)
    db = AsyncEnricherDb(cfg)
    await db.reset_failed_to_retry()
    second = await db.reset_failed_to_retry()
    assert second == {"artists": 0, "albums": 0, "tracks": 0}


@pytest.mark.asyncio
async def test_reset_keeps_matched_reopens_local_only(tmp_path):
    """A fully-matched ENRICHED row (mbid set) is the success state and must
    be left alone; a local-only ENRICHED row (no mbid) is re-opened so a
    matcher improvement can re-attempt it (Phase 2e)."""
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    await _seed(cfg.db_path)
    db = AsyncEnricherDb(cfg)

    await db.reset_failed_to_retry()
    async with aiosqlite.connect(cfg.db_path) as conn:
        async def enriched_of(table, eid):
            cur = await conn.execute(
                f"SELECT enriched FROM {table} WHERE id = ?", (eid,)
            )
            return (await cur.fetchone())[0]

        # Fully matched (mbid) — untouched.
        for table, eid in (("artists", "ar_matched"), ("albums", "al_matched"),
                           ("tracks", "t_matched1"), ("tracks", "t_matched2")):
            assert await enriched_of(table, eid) == 1, f"{eid} should stay ENRICHED"
        # Local-only (no mbid) — re-opened.
        for table, eid in (("artists", "ar_local"), ("albums", "al_local"),
                           ("tracks", "t_local")):
            assert await enriched_of(table, eid) == 0, f"{eid} should be re-opened"


@pytest.mark.asyncio
async def test_reset_on_fresh_db_returns_zeros(tmp_path):
    """A fresh DB has only the seeded sentinel rows; the reset must
    still run cleanly and return zeros."""
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    db = AsyncEnricherDb(cfg)
    counts = await db.reset_failed_to_retry()
    assert counts == {"artists": 0, "albums": 0, "tracks": 0}
