"""Tests for how the FAILED-row retry sweep treats *generated* album art.

Procedural (generated) covers are a last-resort fill for albums no real
source could illustrate. They are marked ``albums.image_generated=1``.
When the enrichment setup changes — a plugin enabled, a version bumped
— the retry sweep must not only re-open the ``enriched=2`` rows but also
strip the generated cover from ``image_generated`` albums and re-open
them, so a real cover an improved matcher can now find replaces the
placeholder. (If nothing better turns up, the deterministic generator
re-derives an identical cover on the next pass.)
"""

from __future__ import annotations

import aiosqlite
import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.enricher.enricher_db import AsyncEnricherDb


def _config(tmp_path) -> LocalFilesConfig:
    return LocalFilesConfig(
        db_path=str(tmp_path / "test.db"),
        artwork_path=str(tmp_path / "artwork"),
    )


async def _seed(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO artists (id, name, enriched) VALUES ('ar1', 'A', 1)"
        )
        await conn.executemany(
            "INSERT INTO albums (id, title, artist_id, enriched, image_url, "
            "image_generated) VALUES (?, ?, ?, ?, ?, ?)",
            [
                # Fully enriched EXCEPT it only has a generated cover.
                ("al_gen_ok", "Gen OK", "ar1", 1, "al_gen_ok", 1),
                # FAILED and also carries a generated cover.
                ("al_gen_failed", "Gen Failed", "ar1", 2, "al_gen_failed", 1),
                # FAILED with a real cover — must NOT be stripped.
                ("al_real", "Real", "ar1", 2, "https://cover/real.jpg", 0),
                # Enriched with a real cover — untouched entirely.
                ("al_done", "Done", "ar1", 1, "https://cover/done.jpg", 0),
            ],
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_schema_has_image_generated_column(tmp_path):
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    async with aiosqlite.connect(cfg.db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(albums)")
        cols = {row[1] for row in await cur.fetchall()}
    assert "image_generated" in cols


@pytest.mark.asyncio
async def test_reset_strips_generated_covers_and_reopens(tmp_path):
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    await _seed(cfg.db_path)
    db = AsyncEnricherDb(cfg)

    # Fingerprint change path: an unconditional reset stands in for it.
    counts = await db.reset_failed_to_retry()

    async with aiosqlite.connect(cfg.db_path) as conn:
        rows = {}
        cur = await conn.execute(
            "SELECT id, enriched, image_url, image_generated FROM albums"
        )
        for r in await cur.fetchall():
            rows[r[0]] = (r[1], r[2], r[3])

    # Both generated albums are re-opened with the cover cleared.
    assert rows["al_gen_ok"] == (0, None, 0)
    assert rows["al_gen_failed"] == (0, None, 0)
    # A real cover on a FAILED album is re-opened but the cover is kept.
    assert rows["al_real"] == (0, "https://cover/real.jpg", 0)
    # A fully enriched album with a real cover is left completely alone.
    assert rows["al_done"] == (1, "https://cover/done.jpg", 0)

    # al_gen_ok (was enriched=1, generated) + al_gen_failed (was
    # enriched=2, generated) + al_real (was enriched=2) are the three
    # album rows reset; al_gen_failed must be counted exactly once.
    assert counts["albums"] == 3


@pytest.mark.asyncio
async def test_no_double_count_for_generated_and_failed(tmp_path):
    """An album that is both FAILED and generated is reset once, not
    twice — the generated-cover pass clears ``enriched`` before the
    per-table FAILED pass can see it again."""
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    async with aiosqlite.connect(cfg.db_path) as conn:
        await conn.execute(
            "INSERT INTO artists (id, name, enriched) VALUES ('ar1', 'A', 1)"
        )
        await conn.execute(
            "INSERT INTO albums (id, title, artist_id, enriched, image_url, "
            "image_generated) VALUES ('al1', 'X', 'ar1', 2, 'al1', 1)"
        )
        await conn.commit()
    db = AsyncEnricherDb(cfg)
    counts = await db.reset_failed_to_retry()
    assert counts["albums"] == 1
