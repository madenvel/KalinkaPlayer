"""What a Jamendo shelf offers to filter by, and how it asks the API for it.

Measured against the live API: ``tags`` is honoured by /tracks/ alone (the
other endpoints drop it and say so in headers.warnings), and multiple tags
intersect rather than union.
"""

import pytest
from kalinka_plugin_sdk.datamodel import EntityId, EntityType
from kalinka_plugin_sdk.filters import FilterKind, FilterOp, FilterQuery, UnsupportedFilter

import kalinka_plugin_jamendo.jamendo as jm
from kalinka_plugin_jamendo.config_model import JamendoConfig


class RecordingClient:
    def __init__(self):
        self.calls = []

    async def request(self, path, params):
        self.calls.append((path, params))
        return []

    async def resolve_audio_url(self, track_id, audioformat):
        return f"https://files.test/{track_id}.{audioformat}"


def _module():
    client = RecordingClient()
    return jm.JamendoInputModule(JamendoConfig(client_id="x"), client), client


def _catalog(slug: str) -> EntityId:
    return EntityId(id=slug, type=EntityType.CATALOG, source="jamendo")


def _query(document) -> FilterQuery:
    return FilterQuery.model_validate(document)



async def test_only_the_track_shelf_offers_genre():
    module, _ = _module()
    root = await module.browse(_catalog("root"))
    declared = {
        item.catalog.id.id: {spec.id for spec in item.catalog.filters}
        for item in root.items
    }

    assert declared["popular-tracks"] == {"q", "genre"}
    assert declared["popular-albums"] == {"q"}
    assert declared["new-releases"] == {"q"}
    assert declared["popular-artists"] == {"q"}
    assert declared["featured-playlists"] == {"q"}


async def test_genre_declares_the_intersection_the_api_actually_does():
    module, _ = _module()
    root = await module.browse(_catalog("root"))
    tracks = next(i for i in root.items if i.catalog.id.id == "popular-tracks")
    genre = next(spec for spec in tracks.catalog.filters if spec.id == "genre")

    assert genre.kind is FilterKind.VALUES
    assert genre.ops == [FilterOp.ALL]


async def test_a_text_field_says_which_name_it_matches():
    module, _ = _module()
    root = await module.browse(_catalog("root"))
    labels = {
        item.catalog.id.id: next(
            spec.label for spec in item.catalog.filters if spec.id == "q"
        )
        for item in root.items
    }

    assert labels["popular-tracks"] == "Search track names"
    assert labels["popular-albums"] == "Search album names"
    assert labels["popular-artists"] == "Search artist names"



async def test_text_narrows_the_shelf_query_and_keeps_its_order():
    module, client = _module()
    await module.browse(_catalog("popular-albums"), filter=_query({"q": {"contains": "miles"}}))

    path, params = client.calls[0]
    assert path == "albums"
    assert params["namesearch"] == "miles"
    assert params["order"] == "popularity_month"


async def test_new_releases_keeps_its_date_window_under_a_filter():
    module, client = _module()
    await module.browse(_catalog("new-releases"), filter=_query({"q": {"contains": "x"}}))

    _, params = client.calls[0]
    assert params["namesearch"] == "x"
    assert "datebetween" in params
    assert params["order"] == "releasedate_desc"


async def test_genres_are_joined_for_the_apis_intersection():
    module, client = _module()
    await module.browse(
        _catalog("popular-tracks"),
        filter=_query({"genre": {"all": ["jazz", "funk"]}}),
    )

    _, params = client.calls[0]
    assert params["tags"] == "jazz funk"


async def test_an_unfiltered_shelf_sends_no_filter_parameters():
    module, client = _module()
    await module.browse(_catalog("popular-tracks"))

    _, params = client.calls[0]
    assert "tags" not in params and "namesearch" not in params



async def test_genre_on_a_shelf_that_cannot_do_it_is_refused():
    module, client = _module()
    with pytest.raises(UnsupportedFilter) as excinfo:
        await module.browse(
            _catalog("popular-albums"), filter=_query({"genre": {"all": ["jazz"]}})
        )

    assert excinfo.value.field == "genre"
    # Refused before the request, so no unfiltered listing goes out.
    assert client.calls == []


async def test_a_union_of_genres_is_refused_rather_than_silently_intersected():
    module, client = _module()
    with pytest.raises(UnsupportedFilter):
        await module.browse(
            _catalog("popular-tracks"), filter=_query({"genre": {"any": ["jazz", "funk"]}})
        )
    assert client.calls == []


async def test_an_undeclared_field_is_refused():
    module, _ = _module()
    with pytest.raises(UnsupportedFilter) as excinfo:
        await module.browse(
            _catalog("popular-tracks"), filter=_query({"year": {"gte": 1990}})
        )
    assert excinfo.value.field == "year"


@pytest.mark.parametrize(
    "type",
    [EntityType.ALBUM, EntityType.ARTIST, EntityType.PLAYLIST],
)
async def test_a_listing_that_offers_no_filters_refuses_one(type):
    module, client = _module()
    with pytest.raises(UnsupportedFilter):
        await module.browse(
            EntityId(id="5", type=type, source="jamendo"),
            filter=_query({"q": {"contains": "miles"}}),
        )
    assert client.calls == []


async def test_the_shelf_index_refuses_a_filter_too():
    module, _ = _module()
    with pytest.raises(UnsupportedFilter):
        await module.browse(_catalog("root"), filter=_query({"q": {"contains": "x"}}))



async def test_vocabulary_is_served_for_the_shelf_that_declares_it():
    module, _ = _module()
    values = await module.list_filter_values(_catalog("popular-tracks"), "genre", 0, 5)

    assert values.total == len(jm.GENRES)
    assert [value.id for value in values.items] == [
        slug for slug, _ in jm.GENRES[:5]
    ]
    # Jamendo cannot say how many tracks a tag covers.
    assert all(value.count is None for value in values.items)


async def test_vocabulary_narrows_by_label_or_slug():
    module, _ = _module()
    by_label = await module.list_filter_values(_catalog("popular-tracks"), "genre", q="hip")
    by_slug = await module.list_filter_values(_catalog("popular-tracks"), "genre", q="rnb")

    assert [value.id for value in by_label.items] == ["hiphop"]
    assert [value.name for value in by_slug.items] == ["R&B"]


async def test_no_vocabulary_where_the_field_is_not_offered():
    module, _ = _module()
    with pytest.raises(UnsupportedFilter):
        await module.list_filter_values(_catalog("popular-albums"), "genre")
    with pytest.raises(UnsupportedFilter):
        await module.list_filter_values(_catalog("popular-tracks"), "year")
