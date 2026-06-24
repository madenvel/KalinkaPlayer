"""Tests for search quality improvements: bulk lookup, FTS AND, dynamic weights, bm25, polling."""

import os
import tempfile

import aiosqlite
import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.searcher.searcher_db import AsyncSearcherDb, _build_fts_query
from kalinka_plugin_localfiles.searcher.searcher import SearchWorker
from kalinka_plugin_localfiles.searcher.query_parser import parse_query


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> LocalFilesConfig:
    return LocalFilesConfig(**overrides)


async def _setup_db(db_path: str) -> AsyncSearcherDb:
    """Create full schema + test data and return an AsyncSearcherDb."""
    config = _make_config(db_path=db_path)
    await init_db(db_path)

    async with aiosqlite.connect(db_path) as conn:
        # Test data
        await conn.execute(
            "INSERT INTO artists (id, name) VALUES ('ar1', 'Miles Davis')"
        )
        await conn.execute(
            "INSERT INTO albums (id, title, artist_id) VALUES ('al1', 'Kind of Blue', 'ar1')"
        )
        for i in range(5):
            await conn.execute(
                "INSERT INTO tracks (id, title, file_path, format, enriched, album_id, artist_id) "
                "VALUES (?, 'track', 'f', 'mp3', 1, 'al1', 'ar1')",
                (f"t{i}",),
            )
        await conn.commit()

    return AsyncSearcherDb(config)


# ---------------------------------------------------------------------------
# Tests: Bulk album/artist lookup
# ---------------------------------------------------------------------------


class TestBulkAlbumArtistLookup:
    @pytest.mark.asyncio
    async def test_returns_all_tracks(self):
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        db = await _setup_db(db_path)

        result = await db.get_tracks_album_artist_bulk(["t0", "t1", "t2"])
        assert len(result) == 3
        assert result["t0"]["album_id"] == "al1"
        assert result["t0"]["artist_id"] == "ar1"

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_list(self):
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        db = await _setup_db(db_path)

        result = await db.get_tracks_album_artist_bulk([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_handles_missing_tracks(self):
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        db = await _setup_db(db_path)

        result = await db.get_tracks_album_artist_bulk(["t0", "nonexistent"])
        assert len(result) == 1
        assert "t0" in result


# ---------------------------------------------------------------------------
# Tests: FTS query strategy (AND-first with OR fallback)
# ---------------------------------------------------------------------------


class TestFtsQueryBuilder:
    def test_and_join_by_default(self):
        result = _build_fts_query("pink floyd")
        assert "AND" in result
        assert '"pink" AND "floyd"' == result

    def test_or_join_explicit(self):
        result = _build_fts_query("pink floyd", join="OR")
        assert '"pink" OR "floyd"' == result

    def test_single_token(self):
        result = _build_fts_query("beatles")
        assert result == '"beatles"'

    def test_short_tokens_filtered(self):
        result = _build_fts_query("a b cd ef")
        assert '"cd" AND "ef"' == result

    def test_empty_query(self):
        assert _build_fts_query("") == ""

    def test_special_chars_cleaned(self):
        result = _build_fts_query('pink "floyd" (live)')
        assert '"pink" AND "floyd" AND "live"' == result


# ---------------------------------------------------------------------------
# Tests: Dynamic weight normalization
# ---------------------------------------------------------------------------


class TestDynamicWeightNormalization:
    def _make_worker(self):
        from unittest.mock import Mock
        config = _make_config()
        db = Mock(spec=AsyncSearcherDb)
        db._vec_available = False
        return SearchWorker(config, db)

    def test_knn_only_uses_full_range(self):
        """With only KNN hits and no tags, a perfect neighbour scores 1.0 —
        the single active weight normalises to the full 0-1 range."""
        worker = self._make_worker()
        parsed = parse_query("beatles")  # no tag constraints

        score = worker._score_track(
            parsed, knn_norm=1.0, track_tags=None, has_knn_hits=True,
        )
        assert score == pytest.approx(1.0, abs=0.01)

    def test_knn_and_tags_normalise_together(self):
        """Tag components blend with KNN; both perfect -> 1.0 after the
        dynamic weight normalisation."""
        worker = self._make_worker()
        parsed = parse_query("jazz piano")  # has genre constraint

        track_tags = {
            "genres": [{"label": "jazz---cool jazz", "score": 0.8}],
        }

        score = worker._score_track(
            parsed, knn_norm=1.0, track_tags=track_tags, has_knn_hits=True,
        )
        # (weight_knn*1 + weight_genre*1) / (weight_knn + weight_genre) = 1.0
        assert score == pytest.approx(1.0, abs=0.01)

    def test_no_active_inputs_returns_zero(self):
        """Edge case: no KNN hits and no tag constraints."""
        worker = self._make_worker()
        parsed = parse_query("something")

        score = worker._score_track(
            parsed, knn_norm=0.0, track_tags=None, has_knn_hits=False,
        )
        assert score == 0.0


# ---------------------------------------------------------------------------
# Helper: seed the FTS index directly (shared by the best-match tests)
# ---------------------------------------------------------------------------


async def _insert_fts_rows(db_path: str, rows: list[tuple[str, str, str, str]]) -> None:
    """Insert (track_id, title, artist_name, album_title) rows into fts_tracks."""
    async with aiosqlite.connect(db_path) as conn:
        for tid, title, artist, album in rows:
            await conn.execute(
                "INSERT INTO fts_tracks (track_id, title, artist_name, album_title, genre_tags) "
                "VALUES (?, ?, ?, ?, ?)",
                (tid, title, artist, album, ""),
            )
        await conn.commit()


# ---------------------------------------------------------------------------
# Tests: BEST MATCH entity-candidate recall
# ---------------------------------------------------------------------------


class TestBestMatchCandidates:
    @pytest.mark.asyncio
    async def test_expands_tracks_into_typed_entities(self):
        """FTS recall yields a track candidate per row plus the de-duplicated
        album and artist, each carrying its own name and relationships."""
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        config = _make_config(db_path=db_path)
        await init_db(db_path)

        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "INSERT INTO artists (id, name) VALUES ('arP', 'The Piano Guys')"
            )
            await conn.execute(
                "INSERT INTO albums (id, title, artist_id) VALUES "
                "('alP', 'The Piano Guys', 'arP')"
            )
            await conn.execute(
                "INSERT INTO tracks (id, title, file_path, format, enriched, "
                "album_id, artist_id) VALUES "
                "('t1', 'A Thousand Years', 'f1', 'mp3', 1, 'alP', 'arP'), "
                "('t2', 'Beethoven Symphony', 'f2', 'mp3', 1, 'alP', 'arP')"
            )
            await conn.commit()

        await _insert_fts_rows(db_path, [
            ("t1", "A Thousand Years", "The Piano Guys", "The Piano Guys"),
            ("t2", "Beethoven Symphony", "The Piano Guys", "The Piano Guys"),
        ])

        db = AsyncSearcherDb(config)
        candidates = await db.search_entity_candidates("piano", limit=50)

        by_type: dict[str, list[dict]] = {"track": [], "album": [], "artist": []}
        for c in candidates:
            by_type[c["type"]].append(c)

        # Two tracks, one deduped album, one deduped artist.
        assert {c["id"] for c in by_type["track"]} == {"t1", "t2"}
        assert [c["id"] for c in by_type["album"]] == ["alP"]
        assert [c["id"] for c in by_type["artist"]] == ["arP"]

        # Relationships carried for the redundancy rules; no scoring done here.
        track = next(c for c in by_type["track"] if c["id"] == "t1")
        assert track["album_id"] == "alP" and track["artist_id"] == "arP"
        assert by_type["album"][0]["artist_id"] == "arP"
        assert all("score" not in c for c in candidates)

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self):
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        config = _make_config(db_path=db_path)
        await init_db(db_path)
        db = AsyncSearcherDb(config)
        assert await db.search_entity_candidates("nonexistent", limit=50) == []
        assert await db.search_entity_candidates("", limit=50) == []


# ---------------------------------------------------------------------------
# Tests: BEST MATCH is separate from the semantic (CLAP) sections
# ---------------------------------------------------------------------------


class TestBestMatchSeparation:
    @pytest.mark.asyncio
    async def test_exact_match_goes_to_best_match_not_ai_tracks(self):
        """The 'vangelis' case: the exact artist match surfaces in the
        BEST MATCH block (literal/navigational), while the AI ``tracks``
        section is purely semantic — the CLAP neighbour "Ben". FTS is no
        longer blended into the semantic ranking, so the two never mix."""
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        config = _make_config(db_path=db_path)
        await init_db(db_path)

        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "INSERT INTO artists (id, name) VALUES "
                "('arV', 'Vangelis'), ('arMJ', 'Michael Jackson')"
            )
            await conn.execute(
                "INSERT INTO albums (id, title, artist_id) VALUES "
                "('alV', 'The Best of Vangelis CD II', 'arV'), "
                "('alMJ', 'The Best Of Michael Jackson (Disk 1)', 'arMJ')"
            )
            await conn.execute(
                "INSERT INTO tracks (id, title, file_path, format, enriched, "
                "album_id, artist_id) VALUES "
                "('tV', 'Chariots of Fire', 'fV', 'mp3', 1, 'alV', 'arV')"
            )
            await conn.execute(
                "INSERT INTO tracks (id, title, file_path, format, enriched, "
                "album_id, artist_id) VALUES "
                "('tMJ', 'Ben', 'fMJ', 'mp3', 1, 'alMJ', 'arMJ')"
            )
            await conn.commit()

        await _insert_fts_rows(db_path, [
            ("tV", "Chariots of Fire", "Vangelis", "The Best of Vangelis CD II"),
            ("tMJ", "Ben", "Michael Jackson",
             "The Best Of Michael Jackson (Disk 1)"),
        ])

        db = AsyncSearcherDb(config)
        worker = SearchWorker(config, db)

        # Force the CLAP leg to return "Ben" as the nearest neighbour.
        async def fake_knn(query, candidate_limit, blob=None):
            return [{"track_id": "tMJ", "distance": 0.0}]

        worker._knn_leg = fake_knn

        # Lowercase query still matches "Vangelis" (case-insensitive scorer).
        result = await worker._do_search("vangelis", limit=10)

        # BEST MATCH leads with the exact artist hit.
        assert result["best_match"][0]["id"] == "arV"
        assert result["best_match"][0]["type"] == "artist"
        # "vangelis" is navigational (near-exact artist name), so the AI
        # suggestion sections are suppressed — BEST MATCH answers it.
        assert result["tracks"] == []

    @pytest.mark.asyncio
    async def test_navigational_suppression_is_descriptor_aware(self):
        """A name match suppresses AI; a descriptor query (even when it matches
        an entity name) keeps it; the config toggle gates the whole thing."""
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        config = _make_config(db_path=db_path)
        await init_db(db_path)
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "INSERT INTO artists (id, name) VALUES ('arMJ', 'Michael Jackson')"
            )
            await conn.execute(
                "INSERT INTO albums (id, title, artist_id) VALUES "
                "('alP', 'Piano Collection', 'arMJ')"
            )
            await conn.execute(
                "INSERT INTO tracks (id, title, file_path, format, enriched, "
                "artist_id, album_id) VALUES "
                "('tMJ', 'Ben', 'fMJ', 'mp3', 1, 'arMJ', NULL), "
                "('tP', 'Etude', 'fP', 'mp3', 1, 'arMJ', 'alP')"
            )
            await conn.commit()
        await _insert_fts_rows(db_path, [
            ("tMJ", "Ben", "Michael Jackson", ""),
            ("tP", "Etude", "Michael Jackson", "Piano Collection"),
        ])

        db = AsyncSearcherDb(config)
        worker = SearchWorker(config, db)

        async def fake_knn(query, candidate_limit, blob=None):
            return [{"track_id": "tMJ", "distance": 0.0}]
        worker._knn_leg = fake_knn

        # Name lookup (not a descriptor) -> AI suppressed.
        assert (await worker._do_search("michael jackson", limit=10))["tracks"] == []
        # Descriptor query that also matches the "Piano Collection" album name ->
        # kept (the whole point: 'piano' is discovery, not a name lookup).
        assert (await worker._do_search("piano", limit=10))["tracks"] == ["tMJ"]
        # Toggle off -> AI returned even for the name lookup.
        config.searcher.suppress_ai_on_navigational = False
        assert (await worker._do_search("michael jackson", limit=10))["tracks"] == ["tMJ"]
