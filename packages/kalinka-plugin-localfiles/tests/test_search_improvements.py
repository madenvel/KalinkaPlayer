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

    def test_fts_only_uses_full_range(self):
        """With only FTS hits, score should be in 0-1 range."""
        worker = self._make_worker()
        parsed = parse_query("beatles")  # no tag constraints

        # Perfect FTS match, no KNN
        score = worker._score_track(
            parsed, fts_rank_norm=1.0, knn_norm=0.0, track_tags=None,
            has_fts_hits=True, has_knn_hits=False,
        )
        # Should be 1.0 (not 0.35 which was the old broken behavior)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_both_legs_active(self):
        """With both FTS and KNN active, weights are split normally."""
        worker = self._make_worker()
        parsed = parse_query("beatles")

        score = worker._score_track(
            parsed, fts_rank_norm=1.0, knn_norm=1.0, track_tags=None,
            has_fts_hits=True, has_knn_hits=True,
        )
        # Both active, no tags: (0.35*1 + 0.30*1) / (0.35+0.30) = 1.0
        assert score == pytest.approx(1.0, abs=0.01)

    def test_with_tag_constraints(self):
        """Tag components contribute when present."""
        worker = self._make_worker()
        parsed = parse_query("jazz piano")  # has genre constraint

        track_tags = {
            "genres": [{"label": "jazz---cool jazz", "score": 0.8}],
        }

        score = worker._score_track(
            parsed, fts_rank_norm=1.0, knn_norm=0.0, track_tags=track_tags,
            has_fts_hits=True, has_knn_hits=False,
        )
        # FTS=1.0 + genre match for "jazz" in "jazz---cool jazz" = 1.0
        assert score > 0.5

    def test_no_active_legs_returns_zero(self):
        """Edge case: no search legs active."""
        worker = self._make_worker()
        parsed = parse_query("something")

        score = worker._score_track(
            parsed, fts_rank_norm=0.0, knn_norm=0.0, track_tags=None,
            has_fts_hits=False, has_knn_hits=False,
        )
        assert score == 0.0


# ---------------------------------------------------------------------------
# Tests: FTS + rapidfuzz re-ranking
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


class TestFtsFuzzyRerank:
    @pytest.mark.asyncio
    async def test_returns_match(self):
        """Exact-title query finds the matching track."""
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        db = await _setup_db(db_path)

        await _insert_fts_rows(db_path, [
            ("t0", "Yesterday", "Beatles", "Help!"),
            ("t1", "Tomorrow Never Knows", "Beatles", "Revolver"),
        ])

        results = await db.fts_search("yesterday", "yesterday", limit=10)
        assert [r["track_id"] for r in results] == ["t0"]

    @pytest.mark.asyncio
    async def test_drops_coincidental_single_token_hit(self):
        """The original AI-search bug: an NL query that incidentally shares
        one common word ("tonight") with a track title must NOT make the
        track pass the fuzzy filter."""
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        db = await _setup_db(db_path)

        await _insert_fts_rows(db_path, [
            ("t0", "Make Tonight All Mine", "Michael Jackson",
             "The Best Of Michael Jackson (Disk 2)"),
            ("t1", "Yesterday", "Beatles", "Help!"),
        ])

        # "tonight" appears in both query and title but the overall
        # fuzzy similarity is well below the 72 threshold.
        results = await db.fts_search(
            "tonight", "something melancholic for tonight", limit=10,
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_keeps_typo_match(self):
        """Single-character typo in a title query still matches via rapidfuzz."""
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        db = await _setup_db(db_path)

        await _insert_fts_rows(db_path, [
            ("t0", "Bohemian Rhapsody", "Queen", "A Night At The Opera"),
        ])

        # FTS5 OR-mode finds it on the correctly-spelled "rhapsody"
        # token; rapidfuzz then approves the score despite "bohemain".
        results = await db.fts_search(
            "rhapsody", "bohemain rhapsody", limit=10,
        )
        assert [r["track_id"] for r in results] == ["t0"]

    @pytest.mark.asyncio
    async def test_min_score_threshold_respected(self):
        """A high threshold drops borderline matches."""
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        db = await _setup_db(db_path)

        await _insert_fts_rows(db_path, [
            ("t0", "Yesterday", "Beatles", "Help!"),
        ])

        # Threshold above any plausible score: even an exact hit drops.
        results = await db.fts_search(
            "yesterday", "yesterday", limit=10, min_score=99,
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_exact_artist_match_is_flagged(self):
        """A query equal to the artist name (case-insensitive) sets the
        ``exact`` flag; a mere title/album substring match does not."""
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        db = await _setup_db(db_path)

        await _insert_fts_rows(db_path, [
            ("t0", "Chariots of Fire", "Vangelis", "The Best of Vangelis CD II"),
            ("t1", "Vangelis Tribute", "Some Cover Band", "Tributes"),
        ])

        results = await db.fts_search("vangelis", "vangelis", limit=10)
        by_id = {r["track_id"]: r for r in results}
        # Exact artist hit flagged regardless of letter case.
        assert by_id["t0"]["exact"] is True
        # Title merely *contains* the token — not an exact field match.
        assert by_id["t1"]["exact"] is False

    @pytest.mark.asyncio
    async def test_ascii_query_matches_accented_artist(self):
        """An ASCII-folded query ("noi kabat") finds an accented artist
        ("Női Kabát"). Without diacritic folding in the rapidfuzz re-rank
        the raw WRatio (~56) falls below the 72 threshold and the candidate
        is dropped even though FTS matched it. See issue #61."""
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        db = await _setup_db(db_path)

        await _insert_fts_rows(db_path, [
            ("t0", "Valami", "Női Kabát", "Best Of"),
            ("t1", "Yesterday", "Beatles", "Help!"),
        ])

        results = await db.fts_search("noi kabat", "noi kabat", limit=10)
        by_id = {r["track_id"]: r for r in results}
        assert "t0" in by_id
        # Diacritic-folded equality also sets the exact-artist flag.
        assert by_id["t0"]["exact"] is True


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
        async def fake_knn(query, candidate_limit):
            return [{"track_id": "tMJ", "distance": 0.0}]

        worker._knn_leg = fake_knn

        result = await worker._do_search("vangelis", limit=10)

        # BEST MATCH leads with the exact artist hit; the album collapses
        # into it (artist dominates) and the unrelated track is cut off.
        assert result["best_match"][0] == {"id": "arV", "type": "artist"}
        # The semantic section is CLAP-only — no FTS bleed-through.
        assert result["tracks"] == ["tMJ"]
        assert "tV" not in result["tracks"]
