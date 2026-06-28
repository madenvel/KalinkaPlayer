"""Tests for the cross-source AI-search assembler (kalinka_server.ai_search).

Covers the responsibilities that moved here from the localfiles searcher:
merged BEST MATCH from every source's ``search()``, navigational suppression of
the semantic suggestions (descriptor-aware), and one AI SUGGESTIONS card per
source (never merged across sources).
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

    bm = _section(result, "BEST MATCH")
    assert bm is not None
    assert [s.name for s in bm.sections] == ["Vangelis"]
    # "vangelis" IS the artist's whole name -> pure name lookup -> AI hidden.
    assert _ai_cards(result) == []
    assert result.items[0] is bm  # BEST MATCH first


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

    bm = _section(result, "BEST MATCH")
    assert bm is not None
    # Artist out-ranks its same-named album -> only the artist survives.
    ids = [s.id.type for s in bm.sections]
    assert ids == [EntityType.ARTIST]


async def test_best_match_merges_across_sources():
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

    bm = _section(result, "BEST MATCH")
    assert bm is not None
    sources = {s.id.source for s in bm.sections}
    assert sources == {"localfiles", "jamendo"}


async def test_large_catalog_does_not_crowd_out_smaller_source():
    # A vast public catalog floods the candidate pool with coincidental, equally
    # high-scoring name matches; the user's library has the one real match. The
    # library match must still land in BEST MATCH (the "jarre" regression). The
    # flooding source is deliberately first to prove order doesn't save it.
    flood = FakeModule(
        "jamendo",
        search_results={
            SearchType.track: [
                _track_item("jamendo", f"j{i}", "jarre") for i in range(8)
            ]
        },
    )
    library = FakeModule(
        "localfiles",
        search_results={
            SearchType.artist: [_artist_item("localfiles", "jmj", "Jean-Michel Jarre")]
        },
    )

    result = await assemble_ai_search([flood, library], "jarre", 0, 10)

    bm = _section(result, "BEST MATCH")
    assert bm is not None
    assert "localfiles" in {s.id.source for s in bm.sections}


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

    assert _section(result, "BEST MATCH") is not None
    assert len(_ai_cards(result)) == 1


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

    assert _section(result, "BEST MATCH") is not None  # "Workout" matched literally
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

    assert _section(result, "BEST MATCH") is None
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
