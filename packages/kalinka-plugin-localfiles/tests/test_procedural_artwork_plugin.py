"""Integration tests for the ProceduralArtworkPlugin enricher leg.

The generator itself is unit-tested under ``test_artwork_*.py``; here we
cover only the enricher-facing contract: it fires only for coverless
albums, writes the three standard artwork sizes to the same location the
downloaded covers use, flags the row ``image_generated``, and loads last
in the enricher's plugin chain.
"""

from __future__ import annotations

import os

import aiosqlite
import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.enricher.enricher import MetadataEnricher
from kalinka_plugin_localfiles.enricher.enricher_db import AsyncEnricherDb
from kalinka_plugin_localfiles.enricher.procedural_artwork_plugin import (
    ProceduralArtworkPlugin,
)


def _config(tmp_path, *, enabled: bool = True) -> LocalFilesConfig:
    cfg = LocalFilesConfig(
        db_path=str(tmp_path / "test.db"),
        artwork_path=str(tmp_path / "artwork"),
    )
    cfg.enricher.plugins.procedural_artwork.enabled = enabled
    return cfg


async def _seed_album(db_path: str, *, image_url=None) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO artists (id, name, enriched) VALUES ('ar1', 'Boards of Canada', 1)"
        )
        await conn.execute(
            "INSERT INTO albums (id, title, artist_id, genre, enriched, image_url) "
            "VALUES ('al1', 'Music Has the Right to Children', 'ar1', 'electronic', 0, ?)",
            (image_url,),
        )
        await conn.executemany(
            "INSERT INTO tracks (id, title, album_id, artist_id, file_path, format, "
            "track_number) VALUES (?, ?, 'al1', 'ar1', ?, 'flac', ?)",
            [
                ("t1", "Wildlife Analysis", "/1", 1),
                ("t2", "An Eagle in Your Mind", "/2", 2),
                ("t3", "The Color of the Fire", "/3", 3),
            ],
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_generates_three_sizes_and_flags_row(tmp_path):
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    await _seed_album(cfg.db_path)
    db = AsyncEnricherDb(cfg)
    plugin = ProceduralArtworkPlugin(cfg, db)

    album = await db.get_album_by_id("al1")
    result = await plugin.enrich_album(album)

    assert result == {"updates": {"image_url": "al1", "image_generated": 1}}
    album_dir = os.path.join(cfg.artwork_path, "album")
    for suffix in ("_large", "_small", "_thumbnail"):
        path = os.path.join(album_dir, f"al1{suffix}.jpg")
        assert os.path.getsize(path) > 0, f"missing/empty {suffix}"


@pytest.mark.asyncio
async def test_skips_album_that_already_has_cover(tmp_path):
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    await _seed_album(cfg.db_path, image_url="https://real/cover.jpg")
    db = AsyncEnricherDb(cfg)
    plugin = ProceduralArtworkPlugin(cfg, db)

    album = await db.get_album_by_id("al1")
    assert await plugin.enrich_album(album) is None
    # Nothing was written for this album.
    assert not os.path.exists(os.path.join(cfg.artwork_path, "album", "al1_large.jpg"))


@pytest.mark.asyncio
async def test_output_is_deterministic(tmp_path):
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    await _seed_album(cfg.db_path)
    db = AsyncEnricherDb(cfg)
    plugin = ProceduralArtworkPlugin(cfg, db)
    album = await db.get_album_by_id("al1")

    await plugin.enrich_album(album)
    large = os.path.join(cfg.artwork_path, "album", "al1_large.jpg")
    first = open(large, "rb").read()
    await plugin.enrich_album(album)  # re-render overwrites in place
    second = open(large, "rb").read()
    assert first == second


@pytest.mark.asyncio
async def test_config_signature_tracks_generator_version(tmp_path):
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    db = AsyncEnricherDb(cfg)
    plugin = ProceduralArtworkPlugin(cfg, db)
    sig = plugin.config_signature()
    assert sig == {"generator_version": plugin._generator.generator_version}


def test_loaded_last_when_enabled(tmp_path):
    """The generator must be the final plugin so every real art source
    runs first."""
    cfg = _config(tmp_path, enabled=True)
    db = AsyncEnricherDb(cfg)
    enricher = MetadataEnricher(cfg, db)
    assert isinstance(enricher.plugins[-1], ProceduralArtworkPlugin)
    # Exactly one instance, and only when enabled.
    assert sum(isinstance(p, ProceduralArtworkPlugin) for p in enricher.plugins) == 1


def test_not_loaded_when_disabled(tmp_path):
    cfg = _config(tmp_path, enabled=False)
    db = AsyncEnricherDb(cfg)
    enricher = MetadataEnricher(cfg, db)
    assert not any(isinstance(p, ProceduralArtworkPlugin) for p in enricher.plugins)
