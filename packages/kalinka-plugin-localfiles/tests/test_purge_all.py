"""Tests for ``LocalFilesInputModuleDb.purge_all``.

The "Rebuild library on next restart" one-shot must wipe *everything* derived
from the library, not just the database — otherwise a rebuild leaves stale
artwork behind. Artwork of every kind (album, procedural album art, playlist
collages, enricher/wikidata images) lives under a single ``artwork_path`` tree,
so purging that tree covers them all. Track embeddings live inside the DB, so
removing the DB file (with its WAL/-shm sidecars) covers those too.
"""

from __future__ import annotations

from pathlib import Path

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.input_module_db import LocalFilesInputModuleDb


def _db(tmp_path) -> LocalFilesInputModuleDb:
    config = LocalFilesConfig(
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
    )
    return LocalFilesInputModuleDb(config)


def test_purge_all_removes_db_with_sidecars_and_all_artwork(tmp_path):
    db = _db(tmp_path)

    # DB file plus its WAL/-shm sidecars.
    for suffix in ("", "-wal", "-shm"):
        Path(str(db.db_path) + suffix).write_bytes(b"x")

    # Every artwork sub-tree that the module writes into.
    for rel in (
        "album/abc_large.jpg",  # downloaded + procedural album art
        "playlist/p1.jpg",  # playlist collage
        "artist/xyz.jpg",  # enricher / wikidata
    ):
        art = db.artwork_path / rel
        art.parent.mkdir(parents=True, exist_ok=True)
        art.write_bytes(b"img")

    db.purge_all()

    for suffix in ("", "-wal", "-shm"):
        assert not Path(str(db.db_path) + suffix).exists()
    assert not db.artwork_path.exists()


def test_purge_all_is_safe_when_nothing_exists(tmp_path):
    # A first-ever boot with the trigger armed must not raise just because the
    # DB and artwork dir don't exist yet.
    db = _db(tmp_path)
    db.purge_all()
    assert not db.db_path.exists()
    assert not db.artwork_path.exists()
