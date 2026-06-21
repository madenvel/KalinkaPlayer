"""Tests for fingerprint-gated FAILED-row retry.

A plain restart used to flip every ``enriched=2`` (FAILED) row back to
``0``, so the enricher re-hammered MusicBrainz/Deezer for rows that
would fail identically. Now the reset is gated on an *enrichment
fingerprint*: the active plugin set (in load order), each plugin's
``ENRICHER_VERSION``, and its match-affecting ``config_signature()``.
FAILED rows are re-opened only when that fingerprint changes — a plugin
enabled/disabled/reordered, a version bumped, or a threshold/API-key
changed.

Two layers are covered:
- the DB gate ``reset_failed_for_fingerprint`` (reset iff changed), and
- ``MetadataEnricher.compute_fingerprint`` (what feeds the gate), in
  particular that bumping a *disabled* plugin is invisible.
"""

from __future__ import annotations

import aiosqlite
import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.enricher.acoustid_plugin import AcoustIdPlugin
from kalinka_plugin_localfiles.enricher.enricher import MetadataEnricher
from kalinka_plugin_localfiles.enricher.enricher_db import AsyncEnricherDb


def _config(tmp_path) -> LocalFilesConfig:
    return LocalFilesConfig(
        db_path=str(tmp_path / "test.db"),
        artwork_path=str(tmp_path / "artwork"),
    )


async def _seed_failed(db_path: str) -> None:
    """Insert a handful of FAILED rows across the three entity tables."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.executemany(
            "INSERT INTO artists (id, name, enriched) VALUES (?, ?, ?)",
            [("ar_f1", "F1", 2), ("ar_f2", "F2", 2)],
        )
        await conn.executemany(
            "INSERT INTO albums (id, title, artist_id, enriched) VALUES (?, ?, ?, ?)",
            [("al_f1", "F1", "ar_f1", 2)],
        )
        await conn.executemany(
            "INSERT INTO tracks (id, title, file_path, format, enriched) "
            "VALUES (?, ?, ?, ?, ?)",
            [("t_f1", "T1", "/f1", "mp3", 2), ("t_f2", "T2", "/f2", "mp3", 2)],
        )
        await conn.commit()


_SEEDED = {
    "artists": ("ar_f1", "ar_f2"),
    "albums": ("al_f1",),
    "tracks": ("t_f1", "t_f2"),
}


async def _mark_seeded_failed(db_path: str) -> None:
    """Flip the seeded rows back to FAILED — simulates new failures
    accumulating during normal operation after a reset. Scoped to the
    seeded ids so init_db's sentinel rows aren't disturbed."""
    async with aiosqlite.connect(db_path) as conn:
        for table, ids in _SEEDED.items():
            placeholders = ", ".join("?" for _ in ids)
            await conn.execute(
                f"UPDATE {table} SET enriched = 2 WHERE id IN ({placeholders})", ids
            )
        await conn.commit()


async def _count_failed(db_path: str) -> int:
    total = 0
    async with aiosqlite.connect(db_path) as conn:
        for table in ("artists", "albums", "tracks"):
            cur = await conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE enriched = 2"
            )
            total += (await cur.fetchone())[0]
    return total


# --------------------------------------------------------------------------
# DB gate: reset_failed_for_fingerprint
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fingerprint_none_on_fresh_db(tmp_path):
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    db = AsyncEnricherDb(cfg)
    assert await db.get_enrichment_fingerprint() is None


@pytest.mark.asyncio
async def test_first_run_resets_and_stores(tmp_path):
    """No stored fingerprint (first run after the feature ships) → reset
    happens and the fingerprint is persisted."""
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    await _seed_failed(cfg.db_path)
    db = AsyncEnricherDb(cfg)

    counts = await db.reset_failed_for_fingerprint("fp-A")
    assert counts == {"artists": 2, "albums": 1, "tracks": 2}
    assert await _count_failed(cfg.db_path) == 0
    assert await db.get_enrichment_fingerprint() == "fp-A"


@pytest.mark.asyncio
async def test_unchanged_fingerprint_is_noop(tmp_path):
    """Same fingerprint on the next restart → no reset, FAILED rows kept,
    and the method signals the skip with ``None``."""
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    await _seed_failed(cfg.db_path)
    db = AsyncEnricherDb(cfg)

    await db.reset_failed_for_fingerprint("fp-A")
    # New FAILED rows accumulate during normal operation after the reset.
    await _mark_seeded_failed(cfg.db_path)
    assert await _count_failed(cfg.db_path) == 5

    result = await db.reset_failed_for_fingerprint("fp-A")
    assert result is None
    assert await _count_failed(cfg.db_path) == 5  # untouched


@pytest.mark.asyncio
async def test_changed_fingerprint_resets_again(tmp_path):
    """A different fingerprint (config/code changed) re-opens FAILED rows
    and stores the new value."""
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    await _seed_failed(cfg.db_path)
    db = AsyncEnricherDb(cfg)

    await db.reset_failed_for_fingerprint("fp-A")
    await _mark_seeded_failed(cfg.db_path)

    counts = await db.reset_failed_for_fingerprint("fp-B")
    assert counts == {"artists": 2, "albums": 1, "tracks": 2}
    assert await _count_failed(cfg.db_path) == 0
    assert await db.get_enrichment_fingerprint() == "fp-B"


# --------------------------------------------------------------------------
# Fingerprint composition: MetadataEnricher.compute_fingerprint
# --------------------------------------------------------------------------


def _enricher(cfg) -> MetadataEnricher:
    return MetadataEnricher(cfg, AsyncEnricherDb(cfg))


def _minimal_config(tmp_path) -> LocalFilesConfig:
    """Config with only MusicBrainz active, so fingerprint tests don't
    spin up the image-fetch plugins' HTTP clients needlessly."""
    cfg = _config(tmp_path)
    cfg.enricher.plugins.musicbrainz.enabled = True
    cfg.enricher.plugins.acoustid.enabled = False
    cfg.enricher.plugins.wikidata.enabled = False
    cfg.enricher.plugins.deezer.enabled = False
    cfg.enricher.plugins.filesystem_fallback_enabled = False
    return cfg


def test_fingerprint_is_stable_for_identical_config(tmp_path):
    a = _enricher(_minimal_config(tmp_path)).compute_fingerprint()
    b = _enricher(_minimal_config(tmp_path)).compute_fingerprint()
    assert a == b


def test_enabling_a_plugin_changes_fingerprint(tmp_path):
    base = _enricher(_minimal_config(tmp_path)).compute_fingerprint()

    cfg = _minimal_config(tmp_path)
    cfg.enricher.plugins.acoustid.enabled = True
    with_acoustid = _enricher(cfg).compute_fingerprint()

    assert base != with_acoustid


def test_changing_match_threshold_changes_fingerprint(tmp_path):
    base = _enricher(_minimal_config(tmp_path)).compute_fingerprint()

    cfg = _minimal_config(tmp_path)
    cfg.enricher.plugins.musicbrainz.track_threshold = 70
    lowered = _enricher(cfg).compute_fingerprint()

    assert base != lowered


def test_acoustid_key_presence_changes_fingerprint(tmp_path):
    cfg_no_key = _minimal_config(tmp_path)
    cfg_no_key.enricher.plugins.acoustid.enabled = True
    no_key = _enricher(cfg_no_key).compute_fingerprint()

    cfg_key = _minimal_config(tmp_path)
    cfg_key.enricher.plugins.acoustid.enabled = True
    cfg_key.enricher.plugins.acoustid.api_key = "secret-key"
    with_key = _enricher(cfg_key).compute_fingerprint()

    assert no_key != with_key
    # The key value itself is never embedded in the fingerprint.
    assert "secret-key" not in with_key


def test_bumping_a_disabled_plugin_is_invisible(tmp_path, monkeypatch):
    """The core property: bumping ``ENRICHER_VERSION`` on a plugin the
    user has *disabled* leaves the fingerprint untouched (no needless
    retry sweep), while bumping it once *enabled* does change it."""
    disabled_v1 = _enricher(_minimal_config(tmp_path)).compute_fingerprint()

    monkeypatch.setattr(AcoustIdPlugin, "ENRICHER_VERSION", 999, raising=True)
    disabled_v999 = _enricher(_minimal_config(tmp_path)).compute_fingerprint()
    assert disabled_v999 == disabled_v1  # AcoustID disabled → bump invisible

    cfg = _minimal_config(tmp_path)
    cfg.enricher.plugins.acoustid.enabled = True
    enabled_v999 = _enricher(cfg).compute_fingerprint()

    monkeypatch.setattr(AcoustIdPlugin, "ENRICHER_VERSION", 1, raising=True)
    enabled_v1 = _enricher(cfg).compute_fingerprint()
    assert enabled_v999 != enabled_v1  # AcoustID enabled → bump visible
