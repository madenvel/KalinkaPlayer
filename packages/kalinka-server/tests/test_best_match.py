"""Tests for the server-side "BEST MATCH" assembly (kalinka_server.best_match).

The scoring/dedup algorithm was lifted verbatim from the localfiles searcher
when BEST MATCH became a cross-source concern; these are its tests, plus
coverage for the BrowseItem -> Entity adapter and the descriptor classifier
that gate the navigational suppression.
"""

from kalinka_plugin_sdk.datamodel import (
    Album,
    Artist,
    BrowseItem,
    EntityId,
    EntityType,
    Track,
)

from kalinka_server import best_match
from kalinka_server.best_match import (
    Entity,
    assemble_best_match,
    browse_item_to_entity,
    coverage_ratio,
    full_match_score,
    has_navigational_intent,
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
        # Both below RAPIDFUZZ_CUTOFF.
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

    def test_playlist_survives_on_its_own_merit(self, monkeypatch):
        # Playlists participate in no dominance rule, so a matching playlist is
        # kept alongside a higher-scoring artist it has no relation to.
        candidates = [
            Entity(id="ar", type="artist", name="Chill Vibes"),
            Entity(id="pl", type="playlist", name="Chill Vibes Mix"),
        ]
        monkeypatch.setattr(best_match, "SCORER", _fixed_scorer([95, 90]))

        result = assemble_best_match(candidates, "q")

        assert {e.id for e in result} == {"ar", "pl"}


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
        # with a title must not clear the cutoff.
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


# ---------------------------------------------------------------------------
# BrowseItem -> Entity adapter
# ---------------------------------------------------------------------------


def _eid(source, etype, local):
    return EntityId(id=local, type=etype, source=source)


class TestBrowseItemToEntity:
    def test_track_carries_album_and_artist_ids(self):
        artist = Artist(id=_eid("localfiles", EntityType.ARTIST, "ar1"), name="A")
        album = Album(
            id=_eid("localfiles", EntityType.ALBUM, "al1"), title="Alb", artist=artist
        )
        item = BrowseItem(
            id=_eid("localfiles", EntityType.TRACK, "t1"),
            name="Song",
            track=Track(
                id=_eid("localfiles", EntityType.TRACK, "t1"),
                title="Song",
                duration=100,
                album=album,
                performer=artist,
            ),
        )

        e = browse_item_to_entity(item)

        assert e.type == "track"
        assert e.id == "kalinka:localfiles:track:t1"
        assert e.album_id == "kalinka:localfiles:album:al1"
        assert e.artist_id == "kalinka:localfiles:artist:ar1"

    def test_album_carries_artist_id_only(self):
        artist = Artist(id=_eid("localfiles", EntityType.ARTIST, "ar1"), name="A")
        item = BrowseItem(
            id=_eid("localfiles", EntityType.ALBUM, "al1"),
            name="Alb",
            album=Album(
                id=_eid("localfiles", EntityType.ALBUM, "al1"),
                title="Alb",
                artist=artist,
            ),
        )

        e = browse_item_to_entity(item)

        assert e.type == "album"
        assert e.album_id is None
        assert e.artist_id == "kalinka:localfiles:artist:ar1"

    def test_ids_are_source_scoped_so_cross_source_never_collide(self):
        # Same local album id from two sources must not be treated as one.
        a = browse_item_to_entity(
            BrowseItem(
                id=_eid("localfiles", EntityType.ALBUM, "1"),
                name="X",
                album=Album(id=_eid("localfiles", EntityType.ALBUM, "1"), title="X"),
            )
        )
        b = browse_item_to_entity(
            BrowseItem(
                id=_eid("jamendo", EntityType.ALBUM, "1"),
                name="X",
                album=Album(id=_eid("jamendo", EntityType.ALBUM, "1"), title="X"),
            )
        )
        assert a.id != b.id


# ---------------------------------------------------------------------------
# Navigational-intent gate (decides whether BEST MATCH / search() runs)
# ---------------------------------------------------------------------------


class TestHasNavigationalIntent:
    def test_names_have_intent(self):
        assert has_navigational_intent("jean michel jarre")
        assert has_navigational_intent("jarre")
        # A descriptor word plus a real name token still counts.
        assert has_navigational_intent("dark side of the moon")
        assert has_navigational_intent("piano guys")

    def test_pure_filler_or_descriptor_has_no_intent(self):
        # The reported case: all tokens are filler/descriptor -> no name.
        assert not has_navigational_intent("something melancholic for tonight")
        assert not has_navigational_intent("piano")
        assert not has_navigational_intent("upbeat jazz")
        assert not has_navigational_intent("play me something relaxing")

    def test_cyrillic_query_has_intent(self):
        # Unicode token regex: a Cyrillic query yields real tokens, so BEST
        # MATCH is not skipped. An ASCII-only regex saw zero tokens -> treated
        # the query as pure-descriptor and skipped the search() legs entirely.
        assert has_navigational_intent("гребенщиков")
        assert has_navigational_intent("борис гребенщиков")


class TestCoverageRatio:
    """The scorer that replaces WRatio: anchored on query-word coverage."""

    def test_partial_query_overlap_scores_low(self):
        # Only 1 of 4 query words matches the title -> well under the cutoff,
        # where WRatio scored it ~90.
        assert coverage_ratio(
            "something melancholic for tonight", "something"
        ) < best_match.RAPIDFUZZ_CUTOFF
        assert coverage_ratio("lofi beats to study", "beats") < best_match.RAPIDFUZZ_CUTOFF

    def test_short_query_fully_in_longer_name_scores_high(self):
        # The whole query is explained by the name; extra name words don't count.
        assert coverage_ratio("jarre", "jean-michel jarre") == 100
        assert coverage_ratio("piano guys", "the piano guys") == 100
        assert coverage_ratio("dark side of the moon", "the dark side of the moon") == 100

    def test_typo_clears_cutoff(self):
        assert coverage_ratio(
            "bohemain rhapsody", "bohemian rhapsody"
        ) >= best_match.RAPIDFUZZ_CUTOFF

    def test_filler_only_query_scores_zero(self):
        assert coverage_ratio("play me something", "something") == 0.0

    def test_cyrillic_name_scores(self):
        # Unicode tokenisation: a Cyrillic fragment fully covered by the name
        # scores 100. An ASCII-only token regex found no name tokens -> 0, so
        # the artist was filtered out of BEST MATCH (inputs arrive casefolded).
        assert coverage_ratio("гребенщиков", "борис гребенщиков") == 100


class TestFullMatchScore:
    """Whole-string similarity that gates AI suppression (cutoff 88)."""

    def test_whole_name_match_is_high(self):
        assert full_match_score("jean michel jarre", "Jean-Michel Jarre") >= 88
        assert full_match_score("the beatles", "The Beatles") >= 88
        assert full_match_score("dark side of the moon", "The Dark Side of the Moon") >= 88
        assert full_match_score("bohemain rhapsody", "Bohemian Rhapsody") >= 88  # typo ok

    def test_partial_or_extra_word_is_low(self):
        assert full_match_score("workout music", "Workout") < 88        # extra "music"
        assert full_match_score("jarre", "Jean-Michel Jarre") < 88      # fragment of name
        assert full_match_score("happy birthday song", "Happy Birthday") < 88
        assert full_match_score("something melancholic for tonight", "Something") < 88
