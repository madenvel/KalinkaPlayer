"""The suggestion assembler: each source's own sections, the library first,
and a source that fails failing the request rather than vanishing."""

from typing import List

import pytest

from kalinka_plugin_sdk.datamodel import (
    Album,
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
from kalinka_plugin_sdk.inputmodule import InputModule

from kalinka_server.ai_search import assemble_ai_search
from kalinka_server.config_model import SearchConfig
from kalinka_server.source_failed import SourceFailed


def _track(source, local, title):
    tid = EntityId(id=local, type=EntityType.TRACK, source=source)
    album = Album(id=EntityId(id=f"al-{local}", type=EntityType.ALBUM, source=source), title="")
    return BrowseItem(
        id=tid,
        name=title,
        can_add=True,
        track=Track(id=tid, title=title, duration=1, album=album),
    )


def _card(source: str, tracks: List[BrowseItem]) -> BrowseItem:
    cat = EntityId(id="ai_search:tracks", type=EntityType.CATALOG, source=source)
    return BrowseItem(
        id=cat,
        name="AI SUGGESTIONS",
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
        sections=tracks,
    )


class _Module(InputModule):
    def __init__(self, name, tracks=None, failing=False):
        self._name = name
        self._tracks = tracks or []
        self._failing = failing
        self.asked = []

    def module_name(self):
        return self._name

    async def ai_search(self, query, offset=0, limit=50):
        self.asked.append(limit)
        if self._failing:
            raise RuntimeError("upstream down")
        if not self._tracks:
            return BrowseItemList(offset=offset, limit=limit, total=0, items=[])
        card = _card(self._name, self._tracks)
        return BrowseItemList(offset=offset, limit=limit, total=1, items=[card])


def _sources(result: BrowseItemList):
    return [item.catalog.sources[0] for item in result.items]


@pytest.mark.asyncio
async def test_one_card_per_source_never_merged():
    jamendo = _Module("jamendo", [_track("jamendo", "j1", "Sunrise")])
    local = _Module("localfiles", [_track("localfiles", "l1", "Sunset")])

    result = await assemble_ai_search([jamendo, local], "calm evening", 0, 10)

    assert _sources(result) == ["localfiles", "jamendo"]
    tracks = [item.id.id for card in result.items for item in card.sections]
    assert tracks == ["l1", "j1"]


@pytest.mark.asyncio
async def test_a_source_with_nothing_to_say_adds_no_section():
    result = await assemble_ai_search(
        [
            _Module("jamendo"),
            _Module("localfiles", [_track("localfiles", "l1", "Sunset")]),
        ],
        "calm evening",
        0,
        10,
    )
    assert _sources(result) == ["localfiles"]


@pytest.mark.asyncio
async def test_the_suggestion_limit_reaches_the_source():
    module = _Module("jamendo", [_track("jamendo", "j1", "Sunrise")])
    await assemble_ai_search(
        [module], "calm", 0, 10, SearchConfig(ai_suggestions_limit=7)
    )
    assert module.asked == [7]


@pytest.mark.asyncio
async def test_a_failing_source_fails_the_request():
    with pytest.raises(SourceFailed) as failure:
        await assemble_ai_search([_Module("jamendo", failing=True)], "calm", 0, 10)
    assert failure.value.source == "jamendo"


@pytest.mark.asyncio
async def test_blank_query_is_empty_without_asking():
    module = _Module("jamendo", [_track("jamendo", "j1", "Sunrise")])
    result = await assemble_ai_search([module], "   ", 0, 10)
    assert result.total == 0
    assert module.asked == []


@pytest.mark.asyncio
async def test_sections_page():
    modules = [
        _Module("a", [_track("a", "a1", "One")]),
        _Module("b", [_track("b", "b1", "Two")]),
        _Module("c", [_track("c", "c1", "Three")]),
    ]
    result = await assemble_ai_search(modules, "calm", 1, 1)
    assert result.total == 3
    assert _sources(result) == ["b"]
