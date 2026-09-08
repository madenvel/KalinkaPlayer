"""Collections as a source: a playlist that says it can be edited, a page that
declares what it narrows by, and nothing it does not own."""

import pytest

from kalinka_plugin_sdk.datamodel import EntityId, EntityType
from kalinka_plugin_sdk.filters import (
    TEXT_FIELD,
    FilterQuery,
    TextSelector,
    UnsupportedFilter,
    ValuesSelector,
)
from kalinka_plugin_sdk.inputmodule import SearchType

from kalinka_server.collections.source import (
    GENRE_FIELD,
    SOURCE_FIELD,
    SOURCE_NAME,
    CollectionsSource,
    catalog_id,
    collection_id,
)
from kalinka_server.collections.store import CollectionStore
from tests.collections_seed import seed_collection, seed_entry, unreadable_snapshot


@pytest.fixture
async def seeded(tmp_path):
    """One collection holding three tracks from two sources."""
    path = str(tmp_path / "collections.db")
    store = CollectionStore(path)
    await store.open()
    await seed_collection(path, "c1", "Road Trip", updated_at=42)
    await seed_entry(
        path, "c1", "e1", source="qobuz", title="Blue", genres=[("jazz", "Jazz")]
    )
    await seed_entry(
        path,
        "c1",
        "e2",
        position=1,
        source="localfiles",
        title="Green",
        genres=[("rock", "Rock")],
    )
    await seed_entry(
        path, "c1", "e3", position=2, source="qobuz", title="Red",
        genres=[("jazz", "Jazz")],
    )
    yield path, CollectionsSource(store, lambda name: name.title())
    await store.close()


def _text(value):
    return FilterQuery({TEXT_FIELD: TextSelector(contains=value)})


class TestShelf:
    async def test_the_root_offers_one_shelf(self, seeded):
        _, source = seeded

        result = await source.browse(catalog_id("root"))

        assert [item.id.id for item in result.items] == ["collections"]
        assert result.items[0].can_browse
        assert result.items[0].name == "Your collections"

    async def test_the_shelf_lists_collections(self, seeded):
        _, source = seeded

        result = await source.browse(catalog_id("collections"))

        assert result.total == 1
        assert result.items[0].name == "Road Trip"

    async def test_the_shelf_narrows_by_name(self, seeded):
        path, source = seeded
        await seed_collection(path, "c2", "Dinner")

        result = await source.browse(catalog_id("collections"), filter=_text("road"))

        assert [item.name for item in result.items] == ["Road Trip"]

    async def test_the_shelf_refuses_a_field_it_never_offered(self, seeded):
        _, source = seeded

        with pytest.raises(UnsupportedFilter):
            await source.browse(
                catalog_id("collections"),
                filter=FilterQuery({GENRE_FIELD: ValuesSelector(any=["jazz"])}),
            )

    async def test_the_playlist_listing_is_the_same_shelf(self, seeded):
        _, source = seeded

        listing = await source.playlist_user_list()

        assert [item.id.id for item in listing.items] == ["c1"]


class TestCollectionItem:
    async def test_it_is_a_playlist_that_says_it_can_be_edited(self, seeded):
        _, source = seeded

        item = (await source.browse(catalog_id("collections"))).items[0]

        assert item.id.type is EntityType.PLAYLIST
        assert (item.can_browse, item.can_add, item.can_edit) == (True, True, True)
        assert item.playlist is not None
        assert item.playlist.track_count == 3
        assert item.subname == "3 tracks"

    async def test_it_is_also_a_page_declaring_what_narrows_it(self, seeded):
        _, source = seeded

        item = (await source.browse(catalog_id("collections"))).items[0]

        assert item.catalog is not None
        assert item.catalog.id == item.id
        assert [spec.id for spec in item.catalog.filters] == [
            TEXT_FIELD,
            SOURCE_FIELD,
            GENRE_FIELD,
        ]

    async def test_it_names_the_sources_it_draws_on(self, seeded):
        _, source = seeded

        item = (await source.browse(catalog_id("collections"))).items[0]
        fetched = await source.get(collection_id("c1"))

        assert item.catalog.sources == ["qobuz", "localfiles"]
        assert fetched.catalog.sources == ["qobuz", "localfiles"]

    async def test_its_timestamp_is_when_it_last_changed(self, seeded):
        _, source = seeded

        item = (await source.browse(catalog_id("collections"))).items[0]

        assert item.timestamp == 42

    async def test_an_empty_one_says_so(self, seeded):
        path, source = seeded
        await seed_collection(path, "c2", "Nothing Yet", updated_at=99)

        item = (await source.browse(catalog_id("collections"))).items[0]

        assert (item.name, item.subname) == ("Nothing Yet", "Empty")
        assert item.catalog.sources == []


class TestBrowsingOne:
    async def test_tracks_keep_the_ids_of_the_sources_that_own_them(self, seeded):
        _, source = seeded

        result = await source.browse(collection_id("c1"))

        assert result.total == 3
        assert [item.id.source for item in result.items] == [
            "qobuz",
            "localfiles",
            "qobuz",
        ]
        assert all(item.id.type is EntityType.TRACK for item in result.items)

    async def test_every_row_carries_its_entry_id(self, seeded):
        _, source = seeded

        result = await source.browse(collection_id("c1"))

        assert [item.track.playlist_track_id for item in result.items] == [
            "e1",
            "e2",
            "e3",
        ]

    async def test_it_narrows_by_source_and_by_genre(self, seeded):
        _, source = seeded

        by_source = await source.browse(
            collection_id("c1"),
            filter=FilterQuery({SOURCE_FIELD: ValuesSelector(any=["localfiles"])}),
        )
        by_genre = await source.browse(
            collection_id("c1"),
            filter=FilterQuery({GENRE_FIELD: ValuesSelector(any=["jazz"])}),
        )

        assert [item.name for item in by_source.items] == ["Green"]
        assert [item.name for item in by_genre.items] == ["Blue", "Red"]

    async def test_a_combination_it_cannot_honour_is_refused(self, seeded):
        _, source = seeded

        with pytest.raises(UnsupportedFilter):
            await source.browse(
                collection_id("c1"),
                filter=FilterQuery({SOURCE_FIELD: ValuesSelector(none=["qobuz"])}),
            )

    async def test_an_undeclared_field_is_refused(self, seeded):
        _, source = seeded

        with pytest.raises(UnsupportedFilter):
            await source.browse(
                collection_id("c1"),
                filter=FilterQuery({"year": ValuesSelector(any=["1999"])}),
            )

    async def test_an_unknown_collection_is_not_an_empty_one(self, seeded):
        _, source = seeded

        with pytest.raises(ValueError):
            await source.browse(collection_id("nope"))

    async def test_one_unreadable_snapshot_does_not_cost_the_rest(self, seeded):
        path, source = seeded
        await seed_entry(
            path, "c1", "e4", position=3, track_json=unreadable_snapshot()
        )

        result = await source.browse(collection_id("c1"))

        assert [item.name for item in result.items] == ["Blue", "Green", "Red"]


class TestWhatClientsReceive:
    """The wire, not the model. Responses are serialised with
    ``exclude_unset``, so a flag an item never set is simply absent — and a
    client reading it as a plain boolean breaks on the whole listing."""

    async def _every_item(self, source):
        root = await source.browse(catalog_id("root"))
        shelf = await source.browse(catalog_id("collections"))
        entries = await source.browse(collection_id("c1"))
        return [*root.items, *shelf.items, *entries.items]

    async def test_every_item_states_whether_it_browses_and_adds(self, seeded):
        _, source = seeded

        for item in await self._every_item(source):
            dumped = item.model_dump(exclude_unset=True)
            assert "can_browse" in dumped, item.name
            assert "can_add" in dumped, item.name

    async def test_only_what_holds_a_changeable_list_says_it_can_be_edited(
        self, seeded
    ):
        """The shelf (which collections exist) and each collection (which
        tracks it holds) — never a track, which is nobody's list."""
        _, source = seeded

        editable = {
            item.name
            for item in await self._every_item(source)
            if item.model_dump(exclude_unset=True).get("can_edit")
        }

        assert editable == {"Your collections", "Road Trip"}


class TestVocabularies:
    async def test_sources_are_named_the_way_the_server_names_them(self, seeded):
        _, source = seeded

        values = await source.list_filter_values(collection_id("c1"), SOURCE_FIELD)

        assert [(v.id, v.name) for v in values.items] == [
            ("qobuz", "Qobuz"),
            ("localfiles", "Localfiles"),
        ]

    async def test_genres_come_from_the_tracks(self, seeded):
        _, source = seeded

        values = await source.list_filter_values(collection_id("c1"), GENRE_FIELD)

        assert [(v.id, v.count) for v in values.items] == [("jazz", 2), ("rock", 1)]

    async def test_a_vocabulary_can_be_searched(self, seeded):
        _, source = seeded

        values = await source.list_filter_values(
            collection_id("c1"), GENRE_FIELD, q="ro"
        )

        assert [v.id for v in values.items] == ["rock"]

    async def test_a_field_with_no_vocabulary_is_refused(self, seeded):
        _, source = seeded

        with pytest.raises(UnsupportedFilter):
            await source.list_filter_values(collection_id("c1"), "year")

    async def test_the_shelf_has_no_vocabulary_of_its_own(self, seeded):
        _, source = seeded

        with pytest.raises(UnsupportedFilter):
            await source.list_filter_values(catalog_id("collections"), SOURCE_FIELD)


class TestSearch:
    async def test_a_name_finds_the_collection(self, seeded):
        _, source = seeded

        result = await source.search(SearchType.playlist, "road")

        assert [item.name for item in result.items] == ["Road Trip"]
        assert result.items[0].can_edit

    async def test_nothing_else_here_has_a_name_of_its_own(self, seeded):
        _, source = seeded

        for kind in (SearchType.track, SearchType.album, SearchType.artist):
            assert (await source.search(kind, "blue")).total == 0

    async def test_a_blank_query_asks_nothing(self, seeded):
        _, source = seeded

        assert (await source.search(SearchType.playlist, "   ")).total == 0


class TestLookup:
    async def test_get_answers_for_a_collection(self, seeded):
        _, source = seeded

        item = await source.get(collection_id("c1"))

        assert item.name == "Road Trip"

    async def test_get_answers_for_the_shelf(self, seeded):
        _, source = seeded

        assert (await source.get(catalog_id("collections"))).can_browse

    async def test_get_refuses_what_it_does_not_hold(self, seeded):
        _, source = seeded

        with pytest.raises(ValueError):
            await source.get(
                EntityId(id="x", type=EntityType.ALBUM, source=SOURCE_NAME)
            )
