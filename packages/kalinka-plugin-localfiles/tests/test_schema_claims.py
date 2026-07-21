#!/usr/bin/env python3
"""Phase 2c schema: the claims/resolution tables and the per-field origin/era
columns exist after init_db, including the migration path on a legacy DB that
predates them.
"""

import aiosqlite
import pytest

from kalinka_plugin_localfiles.db_schema import init_db


async def _cols(conn, table):
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in await cur.fetchall()}


async def _tables(conn):
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {r[0] for r in await cur.fetchall()}


@pytest.mark.asyncio
async def test_claims_tables_and_columns_created(tmp_path):
    db = str(tmp_path / "localfiles.db")
    await init_db(db)
    async with aiosqlite.connect(db) as conn:
        tables = await _tables(conn)
        assert {"metadata_claims", "resolved_origin", "entity_relation"} <= tables
        assert "recording_year" in await _cols(conn, "tracks")
        assert "language" in await _cols(conn, "tracks")
        assert "country" in await _cols(conn, "albums")


@pytest.mark.asyncio
async def test_init_db_is_idempotent(tmp_path):
    # Running init_db twice must not fail (the ALTER-TABLE migrations skip
    # columns that already exist) and must leave the 2c columns in place.
    db = str(tmp_path / "localfiles.db")
    await init_db(db)
    await init_db(db)
    async with aiosqlite.connect(db) as conn:
        assert "recording_year" in await _cols(conn, "tracks")
        assert "language" in await _cols(conn, "tracks")
        assert "country" in await _cols(conn, "albums")


@pytest.mark.asyncio
async def test_metadata_claims_primary_key_is_per_source(tmp_path):
    db = str(tmp_path / "localfiles.db")
    await init_db(db)
    async with aiosqlite.connect(db) as conn:
        # Two sources for the same field coexist (a conflict record).
        await conn.execute(
            "INSERT INTO metadata_claims VALUES (?,?,?,?,?,?,?)",
            ("album", "a1", "album_title", "X", "tag_consensus", "observed", 0),
        )
        await conn.execute(
            "INSERT INTO metadata_claims VALUES (?,?,?,?,?,?,?)",
            ("album", "a1", "album_title", "Y", "folder_name", "inferred", 0),
        )
        await conn.commit()
        cur = await conn.execute(
            "SELECT COUNT(*) FROM metadata_claims WHERE entity_id='a1'"
        )
        assert (await cur.fetchone())[0] == 2
