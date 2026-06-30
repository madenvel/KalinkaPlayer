"""Tests for the cross-source AI-search assembler (kalinka_server.ai_search).

Covers the responsibilities that moved here from the localfiles searcher: a
per-source BEST MATCH section from each source's ``search()``, per-source
full-name suppression of that source's AI suggestions, one AI SUGGESTIONS card
per source, and the derived Related Albums / Artists.
"""

from typing import Dict, List, Optional

from kalinka_plugin_sdk.datamodel import (
    Album,
    Artist,
    BrowseItem,
    BrowseItemList,
    Catalog,
    EntityId,
    EntityType,
    Preview,
    PreviewContentType,
    PreviewType,
    Track,
)
from kalinka_plugin_sdk.inputmodule import SearchType

from kalinka_server.ai_search import assemble_ai_search
from kalinka_server.config_model import SearchConfig


# ---------------------------------------------------------------------------
# Fixtures: BrowseItem builders + a fake input module
# ---------------------------------------------------------------------------


def _artist_item(source: str, local: str, name: str) -> BrowseItem:
    aid = EntityId(id=local, type=EntityType.ARTIST, source=source)
    return BrowseItem(id=aid, name=name, can_browse=True, artist=Artist(id=aid, name=name))


def _album_item(
    source: str, local: str, title: str, artist_local: str, artist_name: str
) -> BrowseItem:
    alid = EntityId(id=local, type=EntityType.ALBUM, source=source)
    arid = EntityId(id=artist_local, type=EntityType.ARTIST, source=source)
    return BrowseItem(
        id=alid,
        name=title,
        can_browse=True,
        album=Album(id=alid, title=title, artist=Artist(id=arid, name=artist_name)),
    )


def _track_item(source: str, local: str, title: str) -> BrowseItem:
    tid = EntityId(id=local, type=EntityType.TRACK, source=source)
    alid = EntityId(id=f"al-{local}", type=EntityType.ALBUM, source=source)
    return BrowseItem(
        id=tid,
        name=title,
        can_add=True,
        track=Track(id=tid, title=title, duration=1, album=Album(id=alid, title="")),
    )


def _sugg_track(source, local, title, album_local, album_title, artist_local, artist_name):
    """A suggestion track carrying full album + artist metadata, so the server
    can derive Related Albums / Related Artists from it."""
    tid = EntityId(id=local, type=EntityType.TRACK, source=source)
    artist = Artist(id=EntityId(id=artist_local, type=EntityType.ARTIST, source=source), name=artist_name)
    album = Album(
        id=EntityId(id=album_local, type=EntityType.ALBUM, source=source),
        title=album_title,
        artist=artist,
    )
    return BrowseItem(
        id=tid,
        name=title,
        can_add=True,
        track=Track(id=tid, title=title, duration=1, album=album, performer=artist),
    )


def _ai_card(source: str, tracks: List[BrowseItem]) -> BrowseItem:
    """Mirror what a plugin's ai_search() returns: a source-scoped, ready-to-
    append "AI SUGGESTIONS" card. The server appends this verbatim."""
    cat = EntityId(id="ai_search:tracks", type=EntityType.CATALOG, source=source)
    return BrowseItem(
        id=cat,
        name="AI SUGGESTIONS",
        subname="Curated for your search",
        catalog=Catalog(
            id=cat,
            title="AI SUGGESTIONS",
            sources=[source],
            preview_config=Preview(
                type=PreviewType.CARD,
                content_type=PreviewContentType.TRACK,
                icon="ai_suggestions",
                items_count=len(tracks),
            ),
        ),
        sections=list(tracks),
    )


class FakeModule:
    """An input module whose search()/ai_search() return canned BrowseItems."""

    def __init__(
        self,
        name: str,
        search_results: Optional[Dict[SearchType, List[BrowseItem]]] = None,
        ai_tracks: Optional[List[BrowseItem]] = None,
    ):
        self._name = name
        self._search = search_results or {}
        self._ai = ai_tracks or []
        self.search_calls = 0

    def module_name(self) -> str:
        return self._name

    def display_name(self) -> str:
        return self._name

    async def search(self, type, query, offset=0, limit=50) -> BrowseItemList:
        self.search_calls += 1
        items = self._search.get(type, [])
        return BrowseItemList(
            offset=offset, limit=limit, total=len(items), items=list(items)
        )

    async def ai_search(self, query, offset=0, limit=50) -> BrowseItemList:
        # Plugins return a ready-made card (or nothing); the server appends it.
        if not self._ai:
            return BrowseItemList(offset=offset, limit=limit, total=0, items=[])
        card = _ai_card(self._name, self._ai)
        return BrowseItemList(offset=offset, limit=limit, total=1, items=[card])


def _section(result: BrowseItemList, name: str) -> Optional[BrowseItem]:
    for item in result.items:
        if item.name == name:
            return item
    return None


def _ai_cards(result: BrowseItemList) -> List[BrowseItem]:
    return [item for item in result.items if item.name == "AI SUGGESTIONS"]


def _best_match_sections(result: BrowseItemList) -> List[BrowseItem]:
    """The per-source BEST MATCH sections (titled 'BEST MATCH · <source>')."""
    return [item for item in result.items if item.name.startswith("BEST MATCH")]


# ---------------------------------------------------------------------------
# BEST MATCH assembly
# ---------------------------------------------------------------------------


async def test_best_match_from_search_results():
    module = FakeModule(
        "localfiles",
        search_results={SearchType.artist: [_artist_item("localfiles", "arV", "Vangelis")]},
        ai_tracks=[_track_item("localfiles", "tMJ", "Ben")],
    )

    result = await assemble_ai_search([module], "vangelis", 0, 10)

    bms = _best_match_sections(result)
    assert [b.name for b in bms] == ["BEST MATCH · localfiles"]
    assert [s.name for s in bms[0].sections] == ["Vangelis"]
    # "vangelis" IS an ARTIST's whole name -> name lookup -> this source's AI hidden.
    assert _ai_cards(result) == []
    assert result.items[0] is bms[0]  # BEST MATCH first


async def test_album_full_match_keeps_ai():
    # "late night jazz" exactly names a Jamendo album, but album/playlist names
    # are often moods — only an ARTIST full-match suppresses, so AI stays.
    module = FakeModule(
        "jamendo",
        search_results={SearchType.album: [
            _album_item("jamendo", "a1", "Late Night Jazz", "ar", "Some Artist")
        ]},
        ai_tracks=[_track_item("jamendo", "t1", "Blue Mood")],
    )

    result = await assemble_ai_search([module], "late night jazz", 0, 10)

    assert len(_best_match_sections(result)) == 1  # the album matched
    assert len(_ai_cards(result)) == 1  # AI NOT hidden (album, not artist)


async def test_artist_dominates_album_in_best_match():
    module = FakeModule(
        "localfiles",
        search_results={
            SearchType.artist: [_artist_item("localfiles", "ar", "Miles Davis")],
            SearchType.album: [
                _album_item("localfiles", "al", "Miles Davis", "ar", "Miles Davis")
            ],
        },
    )

    result = await assemble_ai_search([module], "miles davis", 0, 10)

    bms = _best_match_sections(result)
    assert len(bms) == 1
    # Artist out-ranks its same-named album -> only the artist survives.
    assert [s.id.type for s in bms[0].sections] == [EntityType.ARTIST]


async def test_best_match_is_one_section_per_source():
    local = FakeModule(
        "localfiles",
        search_results={SearchType.artist: [_artist_item("localfiles", "a1", "Daft Punk")]},
    )
    jamendo = FakeModule(
        "jamendo",
        search_results={SearchType.album: [
            _album_item("jamendo", "a2", "Daft Punk", "x", "Tribute")
        ]},
    )

    result = await assemble_ai_search([local, jamendo], "daft punk", 0, 10)

    bms = _best_match_sections(result)
    # One section per source, in module order; each holds only its own match.
    assert [b.name for b in bms] == ["BEST MATCH · localfiles", "BEST MATCH · jamendo"]
    assert {s.id.source for s in bms[0].sections} == {"localfiles"}
    assert {s.id.source for s in bms[1].sections} == {"jamendo"}


async def test_per_source_sections_are_independent_and_capped():
    # Each source has its own BEST MATCH section, so a large catalog can't crowd
    # out a smaller source's match (the old "jarre" regression is structural
    # now). Each section is capped at best_match_max_results (default 3).
    flood = FakeModule(
        "jamendo",
        search_results={SearchType.track: [
            _track_item("jamendo", f"j{i}", "jarre") for i in range(8)
        ]},
    )
    library = FakeModule(
        "localfiles",
        search_results={SearchType.artist: [
            _artist_item("localfiles", "jmj", "Jean-Michel Jarre")
        ]},
    )

    result = await assemble_ai_search([flood, library], "jarre", 0, 10)

    bms = {b.name: b for b in _best_match_sections(result)}
    assert [s.name for s in bms["BEST MATCH · localfiles"].sections] == ["Jean-Michel Jarre"]
    # jamendo's 8 "jarre" tracks are capped to 3; they never displace localfiles.
    assert len(bms["BEST MATCH · jamendo"].sections) == 3


# ---------------------------------------------------------------------------
# AI suggestions: shown unless the query is a near-exact whole-name match
# ---------------------------------------------------------------------------


async def test_partial_name_match_keeps_ai():
    # "piano guys" only partially matches "The Piano Guys" (token_sort 83 < 88,
    # the name's "The" is leftover), so it is NOT a full-string lookup -> AI is
    # still shown below BEST MATCH.
    module = FakeModule(
        "localfiles",
        search_results={SearchType.artist: [
            _artist_item("localfiles", "pg", "The Piano Guys")
        ]},
        ai_tracks=[_track_item("localfiles", "tMJ", "Ben")],
    )

    result = await assemble_ai_search([module], "piano guys", 0, 10)

    assert len(_best_match_sections(result)) == 1
    assert len(_ai_cards(result)) == 1


async def test_suppression_threshold_is_config_driven():
    module = FakeModule(
        "localfiles",
        search_results={SearchType.artist: [
            _artist_item("localfiles", "jmj", "Jean-Michel Jarre")
        ]},
        ai_tracks=[_track_item("localfiles", "t1", "Oxygene")],
    )

    # Default threshold (88): "jean michel jarre" (full-match 94) hides AI.
    default = await assemble_ai_search([module], "jean michel jarre", 0, 10)
    assert _ai_cards(default) == []

    # Raise the threshold above 94 via config -> the same query keeps its AI.
    loose = await assemble_ai_search(
        [module], "jean michel jarre", 0, 10,
        SearchConfig(ai_suppress_full_match_score=95),
    )
    assert len(_best_match_sections(loose)) == 1
    assert len(_ai_cards(loose)) == 1


async def test_unknown_word_query_keeps_ai_even_with_best_match():
    # "workout music": "workout" is not in our word lists, so the gate treats it
    # as navigational and BEST MATCH finds a literal "Workout" track. But the
    # query is not a whole-string match for "Workout" (extra "music",
    # token_sort 70), so the AI suggestions must still be shown. The regression.
    module = FakeModule(
        "jamendo",
        search_results={SearchType.track: [_track_item("jamendo", "w1", "Workout")]},
        ai_tracks=[_track_item("jamendo", "tX", "Morning Run")],
    )

    result = await assemble_ai_search([module], "workout music", 0, 10)

    assert len(_best_match_sections(result)) == 1  # "Workout" matched literally
    assert len(_ai_cards(result)) == 1  # ...and the AI suggestions are NOT hidden


async def test_mood_query_skips_best_match_and_search():
    # A pure mood/filler phrase names nothing: no BEST MATCH, and crucially the
    # search() fan-out is skipped entirely (only ai_search runs) — the latency fix.
    module = FakeModule(
        "jamendo",
        search_results={SearchType.track: [_track_item("jamendo", "s1", "Something")]},
        ai_tracks=[_track_item("jamendo", "tX", "Autumn In The Bog")],
    )

    result = await assemble_ai_search([module], "something melancholic for tonight", 0, 10)

    assert _best_match_sections(result) == []
    assert module.search_calls == 0  # no search() round-trips for a mood query
    assert len(_ai_cards(result)) == 1


# ---------------------------------------------------------------------------
# Per-source AI SUGGESTIONS cards
# ---------------------------------------------------------------------------


async def test_one_ai_card_per_source_not_merged():
    local = FakeModule("localfiles", ai_tracks=[_track_item("localfiles", "t1", "Song A")])
    jamendo = FakeModule("jamendo", ai_tracks=[_track_item("jamendo", "t2", "Song B")])

    result = await assemble_ai_search([local, jamendo], "dreamy ambient", 0, 10)

    cards = _ai_cards(result)
    assert len(cards) == 2
    # One card per source, distinguished by its source-scoped catalog id.
    assert {c.id.source for c in cards} == {"localfiles", "jamendo"}
    # Each card carries only its own source's track.
    for card in cards:
        assert all(t.id.source == card.id.source for t in card.sections)


async def test_empty_when_no_results():
    module = FakeModule("localfiles")
    result = await assemble_ai_search([module], "nothing matches here", 0, 10)
    assert result.total == 0
    assert result.items == []


async def test_blank_query_returns_empty():
    module = FakeModule("localfiles", ai_tracks=[_track_item("localfiles", "t1", "x")])
    result = await assemble_ai_search([module], "   ", 0, 10)
    assert result.total == 0


# ---------------------------------------------------------------------------
# Related Albums / Related Artists (derived from the suggestion tracks)
# ---------------------------------------------------------------------------


async def test_related_albums_and_artists_derived():
    # 2 suggestions on Album One/Artist One, 1 on Album Two/Artist Two.
    module = FakeModule("localfiles", ai_tracks=[
        _sugg_track("localfiles", "t1", "Song 1", "a1", "Album One", "ar1", "Artist One"),
        _sugg_track("localfiles", "t2", "Song 2", "a1", "Album One", "ar1", "Artist One"),
        _sugg_track("localfiles", "t3", "Song 3", "a2", "Album Two", "ar2", "Artist Two"),
    ])

    result = await assemble_ai_search([module], "dreamy ambient", 0, 10)

    ralb = _section(result, "Related Albums")
    rart = _section(result, "Related Artists")
    assert ralb is not None and rart is not None
    # Ranked by suggestion count: the album/artist with 2 tracks leads.
    assert [s.name for s in ralb.sections] == ["Album One", "Album Two"]
    assert [s.name for s in rart.sections] == ["Artist One", "Artist Two"]
    # Browsable cards carrying the entity.
    assert ralb.sections[0].album is not None and ralb.sections[0].can_browse
    assert rart.sections[0].artist is not None
    # Order: suggestion card, then Related Albums, then Related Artists.
    names = [it.name for it in result.items]
    assert names.index("AI SUGGESTIONS") < names.index("Related Albums") < names.index("Related Artists")


async def test_related_hidden_when_ai_suppressed():
    # A full-name lookup hides the suggestions, so the derived rows go too.
    module = FakeModule(
        "localfiles",
        search_results={SearchType.artist: [_artist_item("localfiles", "arV", "Vangelis")]},
        ai_tracks=[
            _sugg_track("localfiles", "t1", "Song", "a1", "Album One", "ar1", "Artist One")
        ],
    )

    result = await assemble_ai_search([module], "vangelis", 0, 10)

    assert len(_best_match_sections(result)) == 1
    assert _ai_cards(result) == []
    assert _section(result, "Related Albums") is None
    assert _section(result, "Related Artists") is None


async def test_catalog_sources_attribute_origin():
    """Every AI-search catalog carries its origin source(s) in ``sources``:
    per-source sections get a single name; the cross-source Related sections
    get the union. id.source stays "server" for the assembled ones, so this is
    the only place the origin is recoverable (no source-id reuse/conflict)."""
    local = FakeModule(
        "localfiles",
        search_results={SearchType.album: [_album_item("localfiles", "al1", "Late Night Jazz", "a1", "Chet")]},
        ai_tracks=[_sugg_track("localfiles", "l1", "Blue", "alX", "Kind of Blue", "aMiles", "Miles Davis")],
    )
    jamendo = FakeModule(
        "jamendo",
        search_results={SearchType.album: [_album_item("jamendo", "al2", "Late Night Jazz", "a2", "Bill")]},
        ai_tracks=[_sugg_track("jamendo", "j1", "So What", "alY", "Jazz Moods", "aBill", "Bill Evans")],
    )
    result = await assemble_ai_search([local, jamendo], "late night jazz", 0, 20)
    cat = {it.name: it.catalog for it in result.items}

    assert cat["BEST MATCH · localfiles"].sources == ["localfiles"]
    assert cat["BEST MATCH · jamendo"].sources == ["jamendo"]
    # Plugin cards carry their own source too (uniform across the response).
    assert cat["AI SUGGESTIONS"].sources  # localfiles or jamendo card
    # Related is rolled up across both sources -> the union.
    assert cat["Related Albums"].sources == ["jamendo", "localfiles"]
    assert cat["Related Artists"].sources == ["jamendo", "localfiles"]
