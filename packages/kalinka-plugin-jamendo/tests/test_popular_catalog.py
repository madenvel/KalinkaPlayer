"""Popular as one catalog of three shelves.

Browsing Popular under ``type: [kind]`` and browsing that kind's shelf are two
addresses for one listing, so the tests hold them to the same request.
"""

import pytest
from kalinka_plugin_sdk.datamodel import (
    EntityId,
    EntityType,
    PreviewContentType,
)
from kalinka_plugin_sdk.filters import FilterQuery, UnsupportedFilter

import kalinka_plugin_jamendo.jamendo as jm
from kalinka_plugin_jamendo.config_model import JamendoConfig


class PagingClient:
    """Serves numbered rows per endpoint, honouring offset and limit, so an
    interleaved page can be read back to the rows each shelf contributed."""

    def __init__(self, sizes=None):
        self._sizes = sizes or {"tracks": 100, "albums": 100, "artists": 100}
        self.calls = []

    async def request(self, path, params):
        self.calls.append((path, params))
        offset, limit = params["offset"], params["limit"]
        stop = min(offset + limit, self._sizes.get(path, 0))
        return [
            {"id": f"{path}-{index}", "name": f"{path} {index}", "duration": "1"}
            for index in range(offset, stop)
        ]


def _module(sizes=None):
    client = PagingClient(sizes)
    return jm.JamendoInputModule(JamendoConfig(client_id="x"), client), client


def _catalog(slug: str) -> EntityId:
    return EntityId(id=slug, type=EntityType.CATALOG, source="jamendo")


def _of_kind(kind: str) -> FilterQuery:
    return FilterQuery.model_validate({"type": {"any": [kind]}})


async def _popular_card(module):
    root = await module.browse(_catalog("root"))
    return next(item for item in root.items if item.id.id == "popular")


async def test_popular_is_one_card_carrying_a_shelf_per_kind():
    module, _ = _module()
    card = await _popular_card(module)

    assert [section.id.id for section in card.sections] == [
        "popular-tracks",
        "popular-albums",
        "popular-artists",
    ]
    assert [section.name for section in card.sections] == [
        "Tracks",
        "Albums",
        "Artists",
    ]


async def test_a_shelf_names_the_kind_it_stands_for():
    # The frontend reads the kind off the preview to know which `type` value
    # "view all" should send.
    module, _ = _module()
    card = await _popular_card(module)
    content_types = [
        section.catalog.preview_config.content_type for section in card.sections
    ]

    assert content_types == [
        PreviewContentType.TRACK,
        PreviewContentType.ALBUM,
        PreviewContentType.ARTIST,
    ]


@pytest.mark.parametrize(
    "kind,shelf,path,order",
    [
        ("track", "popular-tracks", "tracks", "popularity_month"),
        ("album", "popular-albums", "albums", "popularity_month"),
        ("artist", "popular-artists", "artists", "popularity_total"),
    ],
)
async def test_a_kind_asks_exactly_what_its_own_shelf_asks(kind, shelf, path, order):
    module, client = _module()
    await module.browse(_catalog("popular"), limit=20, filter=_of_kind(kind))
    await module.browse(_catalog(shelf), limit=20)

    narrowed, direct = client.calls
    assert narrowed == direct
    assert narrowed[0] == path
    assert narrowed[1]["order"] == order


async def test_a_kind_lists_only_that_kind():
    module, _ = _module()
    page = await module.browse(_catalog("popular"), limit=6, filter=_of_kind("album"))

    assert [item.id.id for item in page.items] == [f"albums-{i}" for i in range(6)]
    assert all(item.album is not None for item in page.items)


async def test_with_no_kind_chosen_the_shelves_are_round_robined():
    module, _ = _module()
    page = await module.browse(_catalog("popular"), limit=6)

    assert [item.id.id for item in page.items] == [
        "tracks-0",
        "albums-0",
        "artists-0",
        "tracks-1",
        "albums-1",
        "artists-1",
    ]


async def test_the_round_robin_pages_without_repeating_or_skipping():
    module, _ = _module()
    first = await module.browse(_catalog("popular"), offset=0, limit=4)
    second = await module.browse(_catalog("popular"), offset=4, limit=4)

    ids = [item.id.id for item in first.items + second.items]
    assert ids == [
        "tracks-0",
        "albums-0",
        "artists-0",
        "tracks-1",
        "albums-1",
        "artists-1",
        "tracks-2",
        "albums-2",
    ]
    assert len(set(ids)) == len(ids)


async def test_a_shelf_that_runs_short_leaves_the_others_in_place():
    # Each position is a fixed shelf's row, so an exhausted shelf drops its
    # positions rather than shifting the rows after it onto another page.
    module, _ = _module({"tracks": 100, "albums": 1, "artists": 100})
    page = await module.browse(_catalog("popular"), limit=6)

    assert [item.id.id for item in page.items] == [
        "tracks-0",
        "albums-0",
        "artists-0",
        "tracks-1",
        "artists-1",
    ]


async def test_a_short_shelf_does_not_end_the_listing_for_the_others():
    # The page is short because albums ran out, but tracks and artists have
    # thousands more; a total that stopped here would hide every one of them.
    module, _ = _module({"tracks": 100, "albums": 1, "artists": 100})
    page = await module.browse(_catalog("popular"), limit=6)

    assert len(page.items) < 6
    assert page.total > len(page.items)


async def test_a_listing_every_shelf_has_run_out_of_says_so():
    module, _ = _module({"tracks": 2, "albums": 1, "artists": 1})
    page = await module.browse(_catalog("popular"), limit=12)

    assert page.total == len(page.items)


async def test_two_kinds_at_once_are_interleaved_between_themselves():
    module, _ = _module()
    page = await module.browse(
        _catalog("popular"),
        limit=4,
        filter=FilterQuery.model_validate({"type": {"any": ["artist", "track"]}}),
    )

    assert [item.id.id for item in page.items] == [
        "artists-0",
        "tracks-0",
        "artists-1",
        "tracks-1",
    ]


async def test_genre_narrows_popular_when_it_is_narrowed_to_tracks():
    module, client = _module()
    await module.browse(
        _catalog("popular"),
        filter=FilterQuery.model_validate(
            {"type": {"any": ["track"]}, "genre": {"all": ["jazz"]}}
        ),
    )

    path, params = client.calls[0]
    assert path == "tracks"
    assert params["tags"] == "jazz"


@pytest.mark.parametrize("kinds", [["album"], ["artist"], ["track", "album"], None])
async def test_genre_is_refused_where_the_listing_is_not_tracks_alone(kinds):
    module, client = _module()
    document = {"genre": {"all": ["jazz"]}}
    if kinds is not None:
        document["type"] = {"any": kinds}

    with pytest.raises(UnsupportedFilter) as excinfo:
        await module.browse(_catalog("popular"), filter=FilterQuery.model_validate(document))

    assert excinfo.value.field == "genre"
    # Refused before the request, so no unfiltered listing goes out.
    assert client.calls == []


async def test_text_narrows_every_kind_it_was_sent_to():
    module, client = _module()
    await module.browse(
        _catalog("popular"), limit=3, filter=FilterQuery.model_validate({"q": {"contains": "miles"}})
    )

    assert {path for path, _ in client.calls} == {"tracks", "albums", "artists"}
    assert all(params["namesearch"] == "miles" for _, params in client.calls)


async def test_an_unknown_kind_is_refused_rather_than_matched_by_nothing():
    module, client = _module()
    with pytest.raises(UnsupportedFilter) as excinfo:
        await module.browse(_catalog("popular"), filter=_of_kind("podcast"))

    assert excinfo.value.field == "type"
    assert client.calls == []


async def test_a_kind_cannot_be_excluded_or_intersected():
    module, _ = _module()
    for selector in ({"none": ["track"]}, {"all": ["track", "album"]}):
        with pytest.raises(UnsupportedFilter) as excinfo:
            await module.browse(
                _catalog("popular"),
                filter=FilterQuery.model_validate({"type": selector}),
            )
        assert excinfo.value.field == "type"


async def test_popular_serves_the_kind_vocabulary_its_shelves_stand_for():
    module, _ = _module()
    values = await module.list_filter_values(_catalog("popular"), "type")

    assert [value.id for value in values.items] == ["track", "album", "artist"]
    assert [value.name for value in values.items] == ["Tracks", "Albums", "Artists"]


async def test_a_shelf_offers_no_kind_vocabulary_of_its_own():
    module, _ = _module()
    with pytest.raises(UnsupportedFilter):
        await module.list_filter_values(_catalog("popular-tracks"), "type")
