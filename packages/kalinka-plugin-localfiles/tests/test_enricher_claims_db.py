#!/usr/bin/env python3
"""AsyncEnricherDb claims persistence (Phase 2d): record_claim / get_claims /
record_resolved_origin round-trip and upsert on the real schema.
"""

import pytest
import pytest_asyncio

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.enricher.enricher_db import AsyncEnricherDb


@pytest_asyncio.fixture
async def db(tmp_path):
    cfg = LocalFilesConfig(
        music_folders=[str(tmp_path)],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "art"),
    )
    await init_db(cfg.db_path)
    return AsyncEnricherDb(cfg)


@pytest.mark.asyncio
async def test_claim_roundtrip_and_upsert(db):
    await db.record_claim("artist", "a1", "name", "The Beatles",
                          "musicbrainz:m1", "inferred")
    await db.record_claim("artist", "a1", "name", "THE BEATLES",
                          "tag_consensus", "observed")
    claims = await db.get_claims("artist", "a1", "name")
    by_source = {c["source"]: c for c in claims}
    assert by_source["musicbrainz:m1"]["value"] == "The Beatles"
    assert by_source["tag_consensus"]["tier"] == "observed"

    # Same (entity, field, source) upserts rather than duplicating.
    await db.record_claim("artist", "a1", "name", "The Beatles!",
                          "musicbrainz:m1", "verified")
    claims = await db.get_claims("artist", "a1", "name")
    assert len(claims) == 2
    mb = next(c for c in claims if c["source"] == "musicbrainz:m1")
    assert mb["value"] == "The Beatles!" and mb["tier"] == "verified"


@pytest.mark.asyncio
async def test_resolved_origin_upsert(db):
    await db.record_resolved_origin("artist", "a1", "name",
                                    "tag_consensus", "observed")
    await db.record_resolved_origin("artist", "a1", "name",
                                    "musicbrainz:m1", "inferred", "release:m1")
    import aiosqlite

    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM resolved_origin WHERE entity_id='a1' AND field='name'"
        )
        rows = [dict(r) for r in await cur.fetchall()]
    assert len(rows) == 1                       # one row per field
    assert rows[0]["source"] == "musicbrainz:m1"
    assert rows[0]["evidence_ref"] == "release:m1"
