"""Tests for the FTS "BEST MATCH" assembly (searcher.best_match)."""

import pytest

from kalinka_plugin_localfiles.searcher import best_match
from kalinka_plugin_localfiles.searcher.best_match import (
    Entity,
    assemble_best_match,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fixed_scorer(scores):
    """A SCORER stub that returns predetermined scores in candidate order.

    assemble_best_match calls SCORER once per candidate, iterating the input
    list in order, so a positional iterator gives each candidate an exact,
    controlled score regardless of its name.
    """
    it = iter(scores)

    def scorer(query, name):
        return next(it)

    return scorer


def _ids(entities):
    return [(e.id, e.type, e.score) for e in entities]


# ---------------------------------------------------------------------------
# Worked example (query: "piano")
# ---------------------------------------------------------------------------


class TestWorkedExample:
    def test_collapses_to_track_and_artist(self, monkeypatch):
        # Order here defines the score alignment for the stub scorer.
        candidates = [
            Entity(id="t-sonata", type="track", name="Piano Sonata",
                   album_id="X", artist_id="Y"),
            Entity(id="PG-artist", type="artist", name="The Piano Guys"),
            Entity(id="PG-album", type="album", name="The Piano Guys",
                   artist_id="PG-artist"),
            Entity(id="t-thousand", type="track", name="A Thousand Years",
                   album_id="PG-album", artist_id="PG-artist"),
        ]
        monkeypatch.setattr(best_match, "SCORER", _fixed_scorer([95, 90, 88, 70]))

        result = assemble_best_match(candidates, "piano")

        assert _ids(result) == [
            ("t-sonata", "track", 95),
            ("PG-artist", "artist", 90),
        ]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_when_nothing_clears_cutoff(self, monkeypatch):
        candidates = [
            Entity(id="a", type="track", name="foo"),
            Entity(id="b", type="album", name="bar"),
        ]
        # Both below RAPIDFUZZ_CUTOFF (70).
        monkeypatch.setattr(best_match, "SCORER", _fixed_scorer([69, 10]))

        assert assemble_best_match(candidates, "q") == []

    def test_fewer_than_six_no_backfill(self, monkeypatch):
        # Seven candidates clear the cutoff; truncation to MAX_RESULTS happens
        # BEFORE redundancy removal, so the dropped redundant entities are not
        # backfilled from the cut-off tail.
        candidates = [
            Entity(id="ar", type="artist", name="Band"),               # 99
            Entity(id="al", type="album", name="Band Album",
                   artist_id="ar"),                                     # 98 -> dominated by artist
            Entity(id="t1", type="track", name="Song 1",
                   album_id="al", artist_id="ar"),                      # 97 -> dominated
            Entity(id="t2", type="track", name="Song 2",
                   album_id="al", artist_id="ar"),                      # 96 -> dominated
            Entity(id="t3", type="track", name="Song 3",
                   album_id="al", artist_id="ar"),                      # 95 -> dominated
            Entity(id="t4", type="track", name="Song 4",
                   album_id="al", artist_id="ar"),                      # 94 -> dominated (6th, in list)
            Entity(id="lonely", type="track", name="Song 5",
                   album_id="other", artist_id="other"),               # 80 -> cut off (7th, truncated)
        ]
        monkeypatch.setattr(
            best_match, "SCORER", _fixed_scorer([99, 98, 97, 96, 95, 94, 80])
        )

        result = assemble_best_match(candidates, "q")

        # Only the artist survives; the truncated "lonely" track is NOT pulled
        # in to fill the gap.
        assert _ids(result) == [("ar", "artist", 99)]

    def test_tie_album_dominates_track(self, monkeypatch):
        # Album and its track score equally (the "wall" case). The album is the
        # container, so it absorbs the tied track (>= dominance) — only the
        # album survives.
        candidates = [
            Entity(id="al", type="album", name="Same", artist_id="ar"),
            Entity(id="t", type="track", name="Same", album_id="al",
                   artist_id="ar"),
        ]
        monkeypatch.setattr(best_match, "SCORER", _fixed_scorer([88, 88]))

        result = assemble_best_match(candidates, "q")

        assert {e.id for e in result} == {"al"}

    def test_track_with_absent_relations_not_removed(self, monkeypatch):
        # A high-scoring album/artist is present but unrelated to the track,
        # and the track's own album/artist are not in the list.
        candidates = [
            Entity(id="ar", type="artist", name="Unrelated"),
            Entity(id="t", type="track", name="Orphan",
                   album_id="missing-al", artist_id="missing-ar"),
            Entity(id="t-no-rel", type="track", name="No Relations",
                   album_id=None, artist_id=None),
        ]
        # Scores all clear the cutoff; the point is the redundancy logic, not
        # the threshold.
        monkeypatch.setattr(best_match, "SCORER", _fixed_scorer([95, 92, 90]))

        result = assemble_best_match(candidates, "q")

        # All three survive: the artist dominates nothing it isn't related to.
        assert {e.id for e in result} == {"ar", "t", "t-no-rel"}


# ---------------------------------------------------------------------------
# End-to-end with the real scorer
# ---------------------------------------------------------------------------


class TestRealScorer:
    def test_ranks_and_cuts_with_wratio(self):
        candidates = [
            Entity(id="ar1", type="artist", name="Miles Davis"),
            Entity(id="al1", type="album", name="Kind of Blue", artist_id="ar1"),
            Entity(id="t1", type="track", name="So What", album_id="al1",
                   artist_id="ar1"),
        ]

        result = assemble_best_match(candidates, "miles davis")

        # The artist match is the clear winner; unrelated track "So What"
        # should fall below the cutoff and be dropped.
        assert result, "expected a non-empty best-match block"
        assert result[0].id == "ar1"
        assert all(e.score >= best_match.RAPIDFUZZ_CUTOFF for e in result)
        assert "t1" not in {e.id for e in result}

    def test_ascii_query_matches_accented_name(self):
        # Diacritic folding: an ASCII query finds an accented artist name.
        candidates = [
            Entity(id="ar1", type="artist", name="Női Kabát"),
            Entity(id="ar2", type="artist", name="The Beatles"),
        ]

        result = assemble_best_match(candidates, "noi kabat")

        assert [e.id for e in result] == ["ar1"]

    def test_drops_coincidental_single_token_hit(self):
        # An NL query that incidentally shares one common word ("tonight")
        # with a title must not clear the cutoff. (Ported from the old FTS
        # re-rank suite — the behaviour now lives in WRatio + the cutoff.)
        candidates = [
            Entity(id="t", type="track", name="Make Tonight All Mine"),
        ]
        result = assemble_best_match(candidates, "something melancholic for tonight")
        assert result == []

    def test_keeps_typo_match(self):
        # A single-character typo still clears the cutoff via WRatio.
        candidates = [Entity(id="t", type="track", name="Bohemian Rhapsody")]
        result = assemble_best_match(candidates, "bohemain rhapsody")
        assert [e.id for e in result] == ["t"]

    def test_custom_cutoff_and_max_results(self):
        candidates = [
            Entity(id="a", type="artist", name="Exact Name"),
            Entity(id="b", type="album", name="Exact Name Deluxe", artist_id="a"),
            Entity(id="c", type="track", name="Totally Different", album_id="b",
                   artist_id="a"),
        ]

        # A near-impossible cutoff keeps only the exact-ish hit; max_results=1
        # truncates before redundancy removal.
        result = assemble_best_match(
            candidates, "Exact Name", cutoff=99, max_results=1
        )

        assert [e.id for e in result] == ["a"]
