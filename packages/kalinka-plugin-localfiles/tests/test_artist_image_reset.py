#!/usr/bin/env python3
"""The retry sweep and artists that have no picture.

An artist MusicBrainz identified is ENRICHED and carries an mbid, so neither
of the sweep's other rules reaches it: it is not FAILED, and it is not
unmatched. That made a broken image source permanent — every artist it had
been asked about was closed for good, and no version bump could re-open them.
The album rule for generated covers has exactly this shape, and artists need
its counterpart.
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
        await conn.executemany(
            "INSERT INTO artists (id, name, enriched, mbid, image_url) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                # Matched and named, but no picture — the case that was stuck.
                ("ar_no_image", "Виктор Цой", 1, "mbid-tsoi", None),
                # Same, with the empty string a older write may have left.
                ("ar_blank", "Иванушки Int", 1, "mbid-iv", ""),
                # Matched and illustrated — nothing to gain, must not churn.
                ("ar_done", "The Beatles", 1, "mbid-beatles", "ar_done"),
            ],
        )
        await conn.commit()


async def _state(db_path: str) -> dict:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT id, enriched FROM artists")
        return {r["id"]: r["enriched"] for r in await cur.fetchall()}


@pytest.mark.asyncio
async def test_an_artist_without_a_picture_is_re_opened(tmp_path):
    config = _config(tmp_path)
    await init_db(config.db_path)
    await _seed(config.db_path)
    db = AsyncEnricherDb(config)

    counts = await db.reset_failed_for_fingerprint("a-new-setup")

    state = await _state(config.db_path)
    assert state["ar_no_image"] == 0
    assert state["ar_blank"] == 0
    # Exactly the two, and not the library-wide placeholder, which the
    # query excludes by name and which is never enriched anyway.
    assert counts["artists"] == 2
    assert state["unknown_artist"] == 0


@pytest.mark.asyncio
async def test_an_illustrated_artist_is_left_alone(tmp_path):
    """Re-opening a row that has what it needs is pure churn."""
    config = _config(tmp_path)
    await init_db(config.db_path)
    await _seed(config.db_path)
    db = AsyncEnricherDb(config)

    await db.reset_failed_for_fingerprint("a-new-setup")

    assert (await _state(config.db_path))["ar_done"] == 1


@pytest.mark.asyncio
async def test_nothing_moves_when_the_setup_is_unchanged(tmp_path):
    config = _config(tmp_path)
    await init_db(config.db_path)
    await _seed(config.db_path)
    db = AsyncEnricherDb(config)

    await db.reset_failed_for_fingerprint("same")
    assert await db.reset_failed_for_fingerprint("same") is None
    assert (await _state(config.db_path))["ar_done"] == 1
