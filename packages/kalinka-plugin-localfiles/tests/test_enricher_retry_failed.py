"""Tests for the once-per-process FAILED-row retry reset.

Background: a track that gets through the enricher pipeline without
hitting all of ``TRACK_REQUIRED_FIELDS`` is marked ``enriched=2``
(FAILED) and the next pass skips it. After plugin code is updated (a
new matcher tier, a bug fix), those rows would stay stuck forever
without intervention. ``AsyncEnricherDb.reset_failed_to_retry`` flips
every FAILED row back to ``0`` and is called once at enricher worker
startup, so a server restart is the natural trigger.
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
    """Insert a mix of enriched/not-enriched/failed rows across all
    three entity tables."""
    async with aiosqlite.connect(db_path) as conn:
        # Artists: 1 not-enriched, 1 enriched, 2 failed.
        await conn.executemany(
            "INSERT INTO artists (id, name, enriched) VALUES (?, ?, ?)",
            [
                ("ar_pending", "Pending", 0),
                ("ar_done", "Done", 1),
                ("ar_failed1", "Failed One", 2),
                ("ar_failed2", "Failed Two", 2),
            ],
        )
        # Albums: 1 enriched, 3 failed.
        await conn.executemany(
            "INSERT INTO albums (id, title, artist_id, enriched) VALUES (?, ?, ?, ?)",
            [
                ("al_done", "Done", "ar_done", 1),
                ("al_failed1", "Failed One", "ar_failed1", 2),
                ("al_failed2", "Failed Two", "ar_failed2", 2),
                ("al_failed3", "Failed Three", "ar_failed1", 2),
            ],
        )
        # Tracks: 2 enriched, 5 failed, 1 not-enriched.
        await conn.executemany(
            "INSERT INTO tracks (id, title, file_path, format, enriched) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("t_done1", "T1", "/f1", "mp3", 1),
                ("t_done2", "T2", "/f2", "mp3", 1),
                ("t_pending", "Tp", "/fp", "mp3", 0),
                ("t_failed1", "Tf1", "/ff1", "mp3", 2),
                ("t_failed2", "Tf2", "/ff2", "mp3", 2),
                ("t_failed3", "Tf3", "/ff3", "mp3", 2),
                ("t_failed4", "Tf4", "/ff4", "mp3", 2),
                ("t_failed5", "Tf5", "/ff5", "mp3", 2),
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
async def test_reset_flips_all_failed_to_pending(tmp_path):
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
    assert counts == {"artists": 2, "albums": 3, "tracks": 5}

    after = await _count_by_status(cfg.db_path)
    # No FAILED rows remain anywhere.
    for table in ("artists", "albums", "tracks"):
        assert 2 not in after[table], f"{table} still has FAILED rows: {after[table]}"
    # ENRICHED count is unchanged — we only touched FAILED.
    assert after["artists"].get(1) == before["artists"].get(1)
    assert after["albums"].get(1) == before["albums"].get(1)
    assert after["tracks"].get(1) == before["tracks"].get(1)
    # Originally-pending tracks plus the reset failed ones are all at 0.
    assert after["tracks"].get(0) == 1 + 5  # 1 pending + 5 reset


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
async def test_reset_does_not_touch_enriched_rows(tmp_path):
    """A row at ``enriched=1`` (ENRICHED) is the success state — the
    reset must leave it alone even though we're updating its sibling
    rows in the same table."""
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    await _seed(cfg.db_path)
    db = AsyncEnricherDb(cfg)

    await db.reset_failed_to_retry()
    async with aiosqlite.connect(cfg.db_path) as conn:
        for entity_id in ("ar_done", "al_done", "t_done1", "t_done2"):
            table = (
                "artists"
                if entity_id.startswith("ar_")
                else "albums" if entity_id.startswith("al_") else "tracks"
            )
            cur = await conn.execute(
                f"SELECT enriched FROM {table} WHERE id = ?", (entity_id,)
            )
            row = await cur.fetchone()
            assert row[0] == 1, f"{entity_id} should still be ENRICHED"


@pytest.mark.asyncio
async def test_reset_on_fresh_db_returns_zeros(tmp_path):
    """A fresh DB has only the seeded sentinel rows; the reset must
    still run cleanly and return zeros."""
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    db = AsyncEnricherDb(cfg)
    counts = await db.reset_failed_to_retry()
    assert counts == {"artists": 0, "albums": 0, "tracks": 0}
