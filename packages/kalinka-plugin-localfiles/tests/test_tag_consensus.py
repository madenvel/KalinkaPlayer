#!/usr/bin/env python3
"""Phase 2g: tag-consensus evidence source (§6.5).

An album's genre/year/language observed value comes from what its tracks' own
tags agree on (read from track_evidence.raw_tags), not from the album row the
indexer seeds off a single track. This is the genuine `observed` claim and it
fills the first-track-seeding gap (album NULL though siblings carry the tag).
"""

import json

import aiosqlite
import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.enricher.enricher import (
    ALBUM_EXTERNAL_FIELDS,
    MetadataEnricher,
)
from kalinka_plugin_localfiles.enricher.enricher_db import AsyncEnricherDb
from kalinka_plugin_localfiles.resolution.tag_consensus import (
    album_tag_consensus,
    extract_fields,
    field_consensus,
)


# --- pure: tag parsing ------------------------------------------------------


def test_extract_id3_frames():
    assert extract_fields({"TDRC": "2016", "TCON": "Electronic"}) == {
        "year": 2016,
        "genre": "Electronic",
    }


def test_extract_vorbis_list_values():
    # Vorbis comments are lower-case and list-valued.
    assert extract_fields(
        {"date": ["1973-03-01"], "genre": ["Progressive Rock"]}
    ) == {"year": 1973, "genre": "Progressive Rock"}


def test_extract_year_from_full_date():
    assert extract_fields({"date": "1982-05"})["year"] == 1982


def test_extract_ignores_unrelated_and_empty():
    assert extract_fields({"TXXX:Tagging time": "2018-06-26", "TENC": "LAME"}) == {}
    assert extract_fields({"genre": [""], "date": None}) == {}
    assert extract_fields(None) == {}


def test_extract_rejects_malformed_year():
    # A stray 4-digit run that isn't a plausible year is dropped, not stored.
    assert "year" not in extract_fields({"date": "0201"})
    assert extract_fields({"TCON": "Rock", "date": "0000"}) == {"genre": "Rock"}


# --- pure: consensus --------------------------------------------------------


def test_consensus_majority():
    assert field_consensus(["Rock", "Rock", "Pop"]) == "Rock"


def test_consensus_case_insensitive_grouping():
    # 'Rock'/'rock' are one group of two, not a tie.
    assert field_consensus(["Rock", "rock", "Pop"]) == "Rock"


def test_consensus_tie_abstains():
    assert field_consensus(["Rock", "Pop"]) is None


def test_consensus_ignores_missing():
    assert field_consensus([1982, 1982, None, ""]) == 1982
    assert field_consensus([]) is None


def test_album_consensus_combines_fields():
    tracks = [
        {"TDRC": "1980", "TCON": "Rock"},
        {"date": ["1980"], "genre": ["Rock"]},
        {"TIT2": "untagged"},  # straggler contributes nothing
    ]
    assert album_tag_consensus(tracks, ("genre", "year", "language")) == {
        "genre": "Rock",
        "year": 1980,
    }


# --- override path in _resolve_external_fields ------------------------------


class _FakeDb:
    def __init__(self):
        self.claims = []
        self.origins = []

    async def record_claim(self, et, eid, field, value, source, tier):
        self.claims.append((et, eid, field, value, source, tier))

    async def get_resolved_origin(self, _et, _eid, _field):
        return None

    async def record_resolved_origin(self, et, eid, field, source, tier, ev=None):
        self.origins.append((et, eid, field, source, tier))


@pytest.mark.asyncio
async def test_override_fills_and_records_tag_consensus_claim():
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.db_manager = _FakeDb()
    album = {"id": "al1", "year": None}          # row gap (first-track seeding)
    updated = {"id": "al1"}
    changed = await enr._resolve_external_fields(
        "album", album, updated, [], ALBUM_EXTERNAL_FIELDS,
        local_overrides={"year": 1973},
    )
    assert changed is True
    assert updated["year"] == 1973
    # The evidence-derived value is persisted as a tag_consensus/observed claim.
    assert ("album", "al1", "year", 1973, "tag_consensus", "observed") \
        in enr.db_manager.claims
    assert ("album", "al1", "year", "tag_consensus", "observed") \
        in enr.db_manager.origins


# --- integration: _enrich_album fills from evidence -------------------------


def _config(tmp_path) -> LocalFilesConfig:
    return LocalFilesConfig(
        db_path=str(tmp_path / "test.db"),
        artwork_path=str(tmp_path / "artwork"),
    )


async def _seed(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO albums (id, title, artist_id, year, genre) "
            "VALUES ('al1', 'A', 'ar1', NULL, NULL)"
        )
        rows = [
            ("t1", '{"TDRC": "1980", "TCON": "Rock"}'),
            ("t2", '{"date": ["1980"], "genre": ["Rock"]}'),
            ("t3", '{"TIT2": "no year/genre here"}'),  # straggler
        ]
        for tid, raw in rows:
            await conn.execute(
                "INSERT INTO tracks (id, title, album_id, artist_id, file_path, "
                "format, enriched) VALUES (?, ?, 'al1', 'ar1', ?, 'flac', 0)",
                (tid, tid, f"/m/{tid}"),
            )
            await conn.execute(
                "INSERT INTO track_evidence (track_id, raw_tags) VALUES (?, ?)",
                (tid, raw),
            )
        await conn.commit()


@pytest.mark.asyncio
async def test_enrich_album_fills_year_genre_from_evidence(tmp_path):
    cfg = _config(tmp_path)
    await init_db(cfg.db_path)
    await _seed(cfg.db_path)
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.db_manager = AsyncEnricherDb(cfg)
    enr.plugins = []  # no external plugins: consensus is the only source

    album = await enr.db_manager.get_album_by_id("al1")
    await enr._enrich_album(album)

    async with aiosqlite.connect(cfg.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        row = dict(await (await conn.execute(
            "SELECT year, genre FROM albums WHERE id='al1'")).fetchone())
        assert row["year"] == 1980
        assert row["genre"] == "Rock"

        origin = await (await conn.execute(
            "SELECT source, tier FROM resolved_origin "
            "WHERE entity_id='al1' AND field='genre'")).fetchone()
        assert (origin["source"], origin["tier"]) == ("tag_consensus", "observed")
