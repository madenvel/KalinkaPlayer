"""Tests for ``AsyncEmbedderDb.restore_snapshot``.

A library rebuild snapshots finished CLAP audio embeddings (see
``test_purge_all``); once the indexer has recreated tracks under their stable
path-hash IDs, the embedder copies the blobs back and records done clap_audio
jobs so the audio model never re-runs for unchanged files. Blobs from an
older model version are left alone — a model bump recomputes as before.
"""

from __future__ import annotations

import aiosqlite
import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.embedder.embedder_db import AsyncEmbedderDb

CURRENT = 4
STALE = 3
SIZE = 4096
MTIME = 1_700_000_000


async def _seed(db_path: str, snapshot_rows, identity=(SIZE, MTIME)) -> AsyncEmbedderDb:
    """Snapshot table as ``purge_all`` leaves it. Rows are given without file
    identity; ``identity`` is appended to each, so a test that cares about a
    changed file passes one that differs from the track's."""
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            CREATE TABLE embedding_snapshot (
                track_id TEXT, embedding_clap_audio BLOB,
                embedding_version INTEGER, embedded_at TIMESTAMP,
                mood_valence REAL, mood_arousal REAL,
                file_size BIGINT, modified_time INTEGER
            )
            """
        )
        await conn.executemany(
            "INSERT INTO embedding_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [tuple(row) + tuple(identity) for row in snapshot_rows],
        )
        await conn.commit()
    return AsyncEmbedderDb(LocalFilesConfig(db_path=db_path))


async def _insert_track(
    db_path: str, track_id: str, size: int = SIZE, mtime: int = MTIME
) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO tracks (id, title, file_path, format, enriched, "
            "file_size, modified_time) VALUES (?, ?, ?, 'flac', 1, ?, ?)",
            (track_id, track_id, f"/m/{track_id}.flac", size, mtime),
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_restore_reattaches_current_version_blobs(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = await _seed(
        db_path,
        [
            ("t1", b"\x01\x02", CURRENT, "2026-01-01 00:00:00", 0.5, -0.25),
            ("t2", b"\x03\x04", STALE, None, None, None),
        ],
    )
    await _insert_track(db_path, "t1")
    await _insert_track(db_path, "t2")

    assert await db.restore_snapshot(CURRENT) == 1

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT embedding_clap_audio, embedding_version, mood_valence, "
            "mood_arousal FROM tracks WHERE id = 't1'"
        )
        assert await cursor.fetchone() == (b"\x01\x02", CURRENT, 0.5, -0.25)

        cursor = await conn.execute(
            "SELECT embedding_clap_audio FROM tracks WHERE id = 't2'"
        )
        assert (await cursor.fetchone())[0] is None

        cursor = await conn.execute(
            "SELECT entity_id, status, model_version FROM embedding_jobs "
            "WHERE stage = 'clap_audio'"
        )
        assert await cursor.fetchall() == [("t1", "done", CURRENT)]

        # Both tracks are back, so neither snapshot row can ever apply again.
        cursor = await conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'embedding_snapshot'"
        )
        assert await cursor.fetchone() is None


@pytest.mark.asyncio
async def test_restored_track_skips_audio_but_gets_text_scheduled(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = await _seed(db_path, [("t1", b"\x01\x02", CURRENT, None, None, None)])
    await _insert_track(db_path, "t1")

    await db.restore_snapshot(CURRENT)
    await db.schedule_new_jobs(CURRENT)

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT stage, status FROM embedding_jobs WHERE entity_id = 't1' "
            "ORDER BY stage"
        )
        assert await cursor.fetchall() == [
            ("clap_audio", "done"),
            ("clap_text", "pending"),
        ]


@pytest.mark.asyncio
async def test_snapshot_dropped_once_fully_consumed(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = await _seed(db_path, [("t1", b"\x01\x02", CURRENT, None, None, None)])
    await _insert_track(db_path, "t1")

    assert await db.restore_snapshot(CURRENT) == 1
    assert await db.restore_snapshot(CURRENT) == 0

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'embedding_snapshot'"
        )
        assert await cursor.fetchone() is None


@pytest.mark.asyncio
async def test_vec_backfill_rebuilds_knn_rows_from_blobs(tmp_path):
    """Regression: the restore must repopulate the KNN index via per-row
    parameter binding — ``vec_int8()`` over a column reference inside
    ``INSERT``...``SELECT`` loses its subtype and vec0 rejects the blob as
    float32, which left the index empty (and AI search blind) after the
    first rebuild in the field."""
    pytest.importorskip("sqlite_vec")
    db_path = str(tmp_path / "test.db")
    blob = bytes(range(256)) * 2
    db = await _seed(db_path, [("t1", blob, CURRENT, None, None, None)])
    await _insert_track(db_path, "t1")

    await db._check_vec_available()
    if not db._vec_available:
        pytest.skip("sqlite-vec extension not loadable")

    assert await db.restore_snapshot(CURRENT) == 1
    assert await db.backfill_missing_vec_rows() == 1
    assert await db.backfill_missing_vec_rows() == 0

    import sqlite_vec

    async with aiosqlite.connect(db_path) as conn:
        await conn.enable_load_extension(True)
        await conn.load_extension(sqlite_vec.loadable_path())
        await conn.enable_load_extension(False)
        cursor = await conn.execute(
            "SELECT track_id, length(embedding) FROM vec_tracks_clap"
        )
        assert await cursor.fetchall() == [("t1", 512)]


@pytest.mark.asyncio
async def test_a_replaced_file_re_embeds_instead_of_inheriting(tmp_path):
    """Track IDs are path hashes, so a different file dropped at the same path
    claims the old ID. Its audio is not the audio we embedded — restoring the
    blob would leave search permanently answering for a file that is gone."""
    db_path = str(tmp_path / "test.db")
    db = await _seed(db_path, [("t1", b"\x01\x02", CURRENT, None, None, None)])
    await _insert_track(db_path, "t1", size=SIZE + 1, mtime=MTIME + 60)

    assert await db.restore_snapshot(CURRENT) == 0

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT embedding_clap_audio FROM tracks WHERE id = 't1'"
        )
        assert (await cursor.fetchone())[0] is None
        cursor = await conn.execute("SELECT 1 FROM embedding_jobs")
        assert await cursor.fetchone() is None

    await db.schedule_new_jobs(CURRENT)
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT status FROM embedding_jobs WHERE stage = 'clap_audio'"
        )
        assert await cursor.fetchall() == [("pending",)]


@pytest.mark.asyncio
async def test_snapshot_without_file_identity_is_discarded(tmp_path):
    """Left by a version that snapshotted no size/mtime: unverifiable, so it
    is dropped and the audio recomputed rather than trusted."""
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            CREATE TABLE embedding_snapshot (
                track_id TEXT, embedding_clap_audio BLOB,
                embedding_version INTEGER, embedded_at TIMESTAMP,
                mood_valence REAL, mood_arousal REAL
            )
            """
        )
        await conn.execute(
            "INSERT INTO embedding_snapshot VALUES ('t1', X'0102', ?, NULL, NULL, NULL)",
            (CURRENT,),
        )
        await conn.commit()
    await _insert_track(db_path, "t1")

    db = AsyncEmbedderDb(LocalFilesConfig(db_path=db_path))
    assert await db.restore_snapshot(CURRENT) == 0

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'embedding_snapshot'"
        )
        assert await cursor.fetchone() is None


@pytest.mark.asyncio
async def test_restore_is_a_noop_without_a_snapshot(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    db = AsyncEmbedderDb(LocalFilesConfig(db_path=db_path))
    assert await db.restore_snapshot(CURRENT) == 0
