"""The collections file: what a listing narrows to, and the facets behind it."""

import pytest

from kalinka_server.collections.store import (
    CollectionChanged,
    CollectionStore,
    EntryFilter,
    NewEntry,
)
from tests.collections_seed import seed_collection as _seed_collection
from tests.collections_seed import seed_entry as _seed_entry


@pytest.fixture
async def store(tmp_path):
    store = CollectionStore(str(tmp_path / "collections.db"))
    await store.open()
    yield store
    await store.close()


def entry(entity_id: str, *, source="qobuz", duration=100, genres=()) -> NewEntry:
    return NewEntry(
        entity_id=entity_id,
        source=source,
        title="A Song",
        artist="A Band",
        album="An Album",
        duration=duration,
        track_json="{}",
        genres=genres,
    )


class TestMakingOne:
    async def test_a_new_collection_is_empty_and_reads_back(self, store):
        row = await store.create_collection("Night Drive", "for the motorway")

        assert (row.name, row.description) == ("Night Drive", "for the motorway")
        assert (row.track_count, row.duration) == (0, 0)
        assert row.created_at == row.updated_at
        assert await store.get_collection(row.id) == row

    async def test_two_collections_may_share_a_name(self, store):
        first = await store.create_collection("Favourites")
        second = await store.create_collection("Favourites")

        assert first.id != second.id
        rows, total = await store.list_collections()
        assert total == 2

    async def test_a_file_that_will_not_open_refuses_the_write(self, tmp_path):
        """A listing degrades to empty; a write must not, or the app reports a
        collection nobody made."""
        path = tmp_path / "collections.db"
        path.write_bytes(b"not a database at all")
        store = CollectionStore(str(path))
        await store.open()

        with pytest.raises(RuntimeError):
            await store.create_collection("Nowhere")


class TestRenaming:
    async def test_a_renamed_collection_reads_back_under_its_new_name(self, store):
        row = await store.create_collection("Night Drive")

        renamed = await store.rename_collection(row.id, "Night Drives")

        assert renamed.name == "Night Drives"
        assert (await store.get_collection(row.id)).name == "Night Drives"

    async def test_renaming_counts_as_changing_it(self, store, tmp_path):
        """What a listing orders by, so a renamed collection leads it."""
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "First", updated_at=10)

        renamed = await store.rename_collection("c1", "First, renamed")

        assert renamed.updated_at > 10

    async def test_renaming_nothing_says_so(self, store):
        assert await store.rename_collection("nobody", "Ghost") is None

    async def test_a_file_that_will_not_open_refuses_the_rename(self, tmp_path):
        path = tmp_path / "collections.db"
        path.write_bytes(b"not a database at all")
        store = CollectionStore(str(path))
        await store.open()

        with pytest.raises(RuntimeError):
            await store.rename_collection("c1", "Nowhere")


class TestDeleting:
    async def test_a_deleted_collection_takes_its_entries_with_it(
        self, store, tmp_path
    ):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Night Drive")
        await _seed_entry(path, "c1", "e0", genres=(("jazz", "Jazz"),))

        assert await store.delete_collection("c1") is True

        rows, total = await store.list_collections()
        assert (rows, total) == ([], 0)
        # The entry went with it, so nothing reads back under its id.
        _, held = await store.list_entries("c1")
        assert held == 0

    async def test_another_collection_is_left_alone(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "One")
        await _seed_collection(path, "c2", "Two")
        await _seed_entry(path, "c2", "theirs")

        await store.delete_collection("c1")

        _, total = await store.list_entries("c2")
        assert (await store.get_collection("c2")).name == "Two"
        assert total == 1

    async def test_deleting_nothing_says_so(self, store):
        assert await store.delete_collection("nobody") is False

    async def test_a_file_that_will_not_open_refuses_the_delete(self, tmp_path):
        path = tmp_path / "collections.db"
        path.write_bytes(b"not a database at all")
        store = CollectionStore(str(path))
        await store.open()

        with pytest.raises(RuntimeError):
            await store.delete_collection("c1")


class TestAdding:
    async def test_tracks_land_at_the_end_in_the_order_given(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Mixed")
        await _seed_entry(path, "c1", "already", position=0)

        outcome = await store.add_entries(
            "c1", [entry("kalinka:qobuz:track:a"), entry("kalinka:qobuz:track:b")]
        )

        rows, total = await store.list_entries("c1")
        assert (outcome.added, outcome.already_there) == (2, 0)
        assert total == 3
        assert [row.entity_id for row in rows][1:] == [
            "kalinka:qobuz:track:a",
            "kalinka:qobuz:track:b",
        ]

    async def test_a_track_already_there_is_left_out(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Mixed")
        await store.add_entries("c1", [entry("kalinka:qobuz:track:a")])

        outcome = await store.add_entries(
            "c1", [entry("kalinka:qobuz:track:a"), entry("kalinka:qobuz:track:b")]
        )

        _, total = await store.list_entries("c1")
        assert (outcome.added, outcome.already_there) == (1, 1)
        assert total == 2

    async def test_one_batch_naming_a_track_twice_adds_it_once(self, store, tmp_path):
        await _seed_collection(str(tmp_path / "collections.db"), "c1", "Mixed")

        outcome = await store.add_entries(
            "c1", [entry("kalinka:qobuz:track:a"), entry("kalinka:qobuz:track:a")]
        )

        assert (outcome.added, outcome.already_there) == (1, 1)

    async def test_a_track_may_be_added_twice_when_asked_for(self, store, tmp_path):
        await _seed_collection(str(tmp_path / "collections.db"), "c1", "Mixed")

        outcome = await store.add_entries(
            "c1",
            [entry("kalinka:qobuz:track:a"), entry("kalinka:qobuz:track:a")],
            allow_duplicates=True,
        )

        rows, total = await store.list_entries("c1")
        assert (outcome.added, outcome.already_there) == (2, 0)
        assert total == 2
        assert rows[0].entry_id != rows[1].entry_id

    async def test_what_was_added_is_what_the_facets_count(self, store, tmp_path):
        await _seed_collection(str(tmp_path / "collections.db"), "c1", "Mixed")

        await store.add_entries(
            "c1",
            [
                entry("kalinka:qobuz:track:a", genres=(("jazz", "Jazz"),)),
                entry(
                    "kalinka:localfiles:track:b",
                    source="localfiles",
                    genres=(("jazz", "JAZZ"),),
                ),
            ],
        )

        sources = await store.sources_of("c1")
        genres = await store.genres_of("c1")
        assert {value.id for value in sources} == {"qobuz", "localfiles"}
        assert [(value.id, value.count) for value in genres] == [("jazz", 2)]

    async def test_adding_counts_as_changing_it(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "First", updated_at=10)

        await store.add_entries("c1", [entry("kalinka:qobuz:track:a")])

        assert (await store.get_collection("c1")).updated_at > 10

    async def test_adding_to_nothing_says_so(self, store):
        assert await store.add_entries("nobody", [entry("x")]) is None

    async def test_a_file_that_will_not_open_refuses_the_add(self, tmp_path):
        path = tmp_path / "collections.db"
        path.write_bytes(b"not a database at all")
        store = CollectionStore(str(path))
        await store.open()

        with pytest.raises(RuntimeError):
            await store.add_entries("c1", [entry("kalinka:qobuz:track:a")])


class TestReplacing:
    async def test_the_collection_ends_up_holding_exactly_what_was_given(
        self, store, tmp_path
    ):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Mixed")
        await _seed_entry(path, "c1", "old1", position=0)
        await _seed_entry(path, "c1", "old2", position=1)

        outcome = await store.replace_entries(
            "c1", [entry("kalinka:qobuz:track:a"), entry("kalinka:qobuz:track:b")]
        )

        rows, total = await store.list_entries("c1")
        assert (outcome.added, outcome.dropped) == (2, 2)
        assert total == 2
        assert [row.entity_id for row in rows] == [
            "kalinka:qobuz:track:a",
            "kalinka:qobuz:track:b",
        ]

    async def test_one_batch_naming_a_track_twice_writes_it_once(
        self, store, tmp_path
    ):
        await _seed_collection(str(tmp_path / "collections.db"), "c1", "Mixed")

        outcome = await store.replace_entries(
            "c1", [entry("kalinka:qobuz:track:a"), entry("kalinka:qobuz:track:a")]
        )

        _, total = await store.list_entries("c1")
        assert outcome.added == 1
        assert total == 1

    async def test_a_track_may_be_written_twice_when_asked_for(self, store, tmp_path):
        await _seed_collection(str(tmp_path / "collections.db"), "c1", "Mixed")

        outcome = await store.replace_entries(
            "c1",
            [entry("kalinka:qobuz:track:a"), entry("kalinka:qobuz:track:a")],
            allow_duplicates=True,
        )

        rows, total = await store.list_entries("c1")
        assert outcome.added == 2
        assert total == 2
        assert rows[0].entry_id != rows[1].entry_id

    async def test_what_was_dropped_takes_its_genres_with_it(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Mixed")
        await _seed_entry(path, "c1", "old1", genres=[("rock", "Rock")])

        await store.replace_entries(
            "c1", [entry("kalinka:qobuz:track:a", genres=(("jazz", "Jazz"),))]
        )

        assert [value.id for value in await store.genres_of("c1")] == ["jazz"]

    async def test_replacing_starts_the_positions_again(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Mixed")
        for i in range(5):
            await _seed_entry(path, "c1", f"old{i}", position=i)

        await store.replace_entries("c1", [entry("kalinka:qobuz:track:a")])

        rows, _ = await store.list_entries("c1")
        assert [row.position for row in rows] == [0]

    async def test_another_collection_keeps_what_it_had(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "One")
        await _seed_collection(path, "c2", "Two")
        await _seed_entry(path, "c2", "theirs")

        await store.replace_entries("c1", [entry("kalinka:qobuz:track:a")])

        _, total = await store.list_entries("c2")
        assert total == 1

    async def test_replacing_counts_as_changing_it(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "First", updated_at=10)

        await store.replace_entries("c1", [entry("kalinka:qobuz:track:a")])

        assert (await store.get_collection("c1")).updated_at > 10

    async def test_replacing_nothing_says_so(self, store):
        assert await store.replace_entries("nobody", [entry("x")]) is None

    async def test_a_file_that_will_not_open_refuses_the_replace(self, tmp_path):
        path = tmp_path / "collections.db"
        path.write_bytes(b"not a database at all")
        store = CollectionStore(str(path))
        await store.open()

        with pytest.raises(RuntimeError):
            await store.replace_entries("c1", [entry("kalinka:qobuz:track:a")])


class TestEditing:
    """A staged edit: what the editing session commits in one write."""

    async def _four(self, tmp_path, genres=()):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Mixed")
        for i in range(4):
            await _seed_entry(path, "c1", f"e{i}", position=i, genres=genres)
        return path

    async def test_what_is_removed_goes_and_the_rest_close_up(self, store, tmp_path):
        await self._four(tmp_path)

        outcome = await store.edit_entries("c1", ["e1"], ["e0", "e2", "e3"])

        rows, total = await store.list_entries("c1")
        assert (outcome.removed, outcome.moved) == (1, 0)
        assert total == 3
        assert [row.entry_id for row in rows] == ["e0", "e2", "e3"]
        assert [row.position for row in rows] == [0, 1, 2]

    async def test_the_order_given_is_the_order_read_back(self, store, tmp_path):
        await self._four(tmp_path)

        outcome = await store.edit_entries("c1", [], ["e3", "e0", "e2", "e1"])

        rows, _ = await store.list_entries("c1")
        assert [row.entry_id for row in rows] == ["e3", "e0", "e2", "e1"]
        assert outcome.removed == 0
        # e2 alone kept its place, so three of the four places changed.
        assert outcome.moved == 3

    async def test_closing_up_after_a_removal_is_not_a_move(self, store, tmp_path):
        """Removing the first row shifts every other one up, and none of that
        is something the user did."""
        await self._four(tmp_path)

        outcome = await store.edit_entries("c1", ["e0"], ["e1", "e2", "e3"])

        assert outcome.moved == 0

    async def test_one_moved_row_counts_the_places_it_disturbed(
        self, store, tmp_path
    ):
        await self._four(tmp_path)

        outcome = await store.edit_entries("c1", [], ["e1", "e0", "e2", "e3"])

        assert (outcome.removed, outcome.moved) == (0, 2)

    async def test_what_is_dropped_takes_its_genres_with_it(self, store, tmp_path):
        await self._four(tmp_path, genres=(("jazz", "Jazz"),))

        await store.edit_entries("c1", ["e0", "e1", "e2"], ["e3"])

        genres = await store.genres_of("c1")
        assert [(value.id, value.count) for value in genres] == [("jazz", 1)]

    async def test_an_edit_that_leaves_an_entry_unaccounted_for_is_refused(
        self, store, tmp_path
    ):
        await self._four(tmp_path)

        with pytest.raises(CollectionChanged):
            await store.edit_entries("c1", [], ["e0", "e1", "e2"])

        rows, total = await store.list_entries("c1")
        assert total == 4
        assert [row.entry_id for row in rows] == ["e0", "e1", "e2", "e3"]

    async def test_an_edit_naming_something_that_is_not_there_is_refused(
        self, store, tmp_path
    ):
        await self._four(tmp_path)

        with pytest.raises(CollectionChanged):
            await store.edit_entries("c1", [], ["e0", "e1", "e2", "gone"])

    async def test_an_edit_naming_one_entry_twice_is_refused(self, store, tmp_path):
        await self._four(tmp_path)

        with pytest.raises(CollectionChanged):
            await store.edit_entries("c1", [], ["e0", "e0", "e2", "e3"])

    async def test_removing_every_entry_leaves_the_collection_standing(
        self, store, tmp_path
    ):
        await self._four(tmp_path)

        outcome = await store.edit_entries("c1", ["e0", "e1", "e2", "e3"], [])

        assert outcome.removed == 4
        assert (await store.get_collection("c1")).track_count == 0

    async def test_another_collection_is_left_alone(self, store, tmp_path):
        path = await self._four(tmp_path)
        await _seed_collection(path, "c2", "Two")
        await _seed_entry(path, "c2", "theirs")

        await store.edit_entries("c1", ["e0"], ["e1", "e2", "e3"])

        _, total = await store.list_entries("c2")
        assert total == 1

    async def test_editing_counts_as_changing_it(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "First", updated_at=10)
        await _seed_entry(path, "c1", "e0", position=0)

        await store.edit_entries("c1", ["e0"], [])

        assert (await store.get_collection("c1")).updated_at > 10

    async def test_editing_nothing_says_so(self, store):
        assert await store.edit_entries("nobody", [], []) is None

    async def test_a_file_that_will_not_open_refuses_the_edit(self, tmp_path):
        path = tmp_path / "collections.db"
        path.write_bytes(b"not a database at all")
        store = CollectionStore(str(path))
        await store.open()

        with pytest.raises(RuntimeError):
            await store.edit_entries("c1", [], [])


class TestCollections:
    async def test_the_most_recently_changed_leads(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "old", "Old One", updated_at=10)
        await _seed_collection(path, "new", "New One", updated_at=20)

        rows, total = await store.list_collections()

        assert total == 2
        assert [row.id for row in rows] == ["new", "old"]

    async def test_totals_come_from_the_entries(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Mixed")
        await _seed_entry(path, "c1", "e1", duration=100)
        await _seed_entry(path, "c1", "e2", position=1, duration=250)

        row = await store.get_collection("c1")

        assert row is not None
        assert (row.track_count, row.duration) == (2, 350)

    async def test_an_empty_collection_reads_back(self, store, tmp_path):
        await _seed_collection(str(tmp_path / "collections.db"), "c1", "Empty")

        row = await store.get_collection("c1")

        assert row is not None
        assert row.track_count == 0

    async def test_a_name_narrows_by_every_token(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Sunday Morning Jazz")
        await _seed_collection(path, "c2", "Sunday Drive")

        rows, total = await store.list_collections(text="sunday jazz")

        assert total == 1
        assert [row.id for row in rows] == ["c1"]

    async def test_a_wildcard_in_the_text_is_a_character(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Anything")
        await _seed_collection(path, "c2", "100% Cotton")

        rows, _ = await store.list_collections(text="100%")

        assert [row.id for row in rows] == ["c2"]

    async def test_an_unknown_collection_is_none(self, store):
        assert await store.get_collection("nope") is None


class TestEntries:
    async def test_entries_keep_their_order(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Mixed")
        await _seed_entry(path, "c1", "e2", position=1, title="Second")
        await _seed_entry(path, "c1", "e1", position=0, title="First")

        rows, total = await store.list_entries("c1")

        assert total == 2
        assert [row.entry_id for row in rows] == ["e1", "e2"]

    async def test_paging_holds_the_total(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Mixed")
        for i in range(5):
            await _seed_entry(path, "c1", f"e{i}", position=i)

        rows, total = await store.list_entries("c1", offset=3, limit=2)

        assert total == 5
        assert [row.entry_id for row in rows] == ["e3", "e4"]

    async def test_text_reaches_title_artist_and_album(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Mixed")
        await _seed_entry(path, "c1", "e1", title="Blue", artist="Miles", album="Kind")
        await _seed_entry(path, "c1", "e2", position=1, title="Red", artist="Nina")

        by_artist, _ = await store.list_entries(
            "c1", listing=EntryFilter(text="miles")
        )
        by_album, _ = await store.list_entries("c1", listing=EntryFilter(text="kind"))

        assert [row.entry_id for row in by_artist] == ["e1"]
        assert [row.entry_id for row in by_album] == ["e1"]

    async def test_sources_union_and_genres_intersect_them(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Mixed")
        await _seed_entry(
            path, "c1", "e1", source="qobuz", genres=[("jazz", "Jazz")]
        )
        await _seed_entry(
            path, "c1", "e2", position=1, source="localfiles",
            genres=[("jazz", "Jazz")],
        )
        await _seed_entry(
            path, "c1", "e3", position=2, source="qobuz", genres=[("rock", "Rock")]
        )

        both, _ = await store.list_entries(
            "c1", listing=EntryFilter(sources=("qobuz", "localfiles"))
        )
        narrowed, _ = await store.list_entries(
            "c1", listing=EntryFilter(sources=("qobuz",), genres=("jazz",))
        )

        assert [row.entry_id for row in both] == ["e1", "e2", "e3"]
        assert [row.entry_id for row in narrowed] == ["e1"]

    async def test_another_collections_entries_are_not_listed(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "One")
        await _seed_collection(path, "c2", "Two")
        await _seed_entry(path, "c1", "e1")
        await _seed_entry(path, "c2", "e2")

        rows, total = await store.list_entries("c1")

        assert total == 1
        assert [row.entry_id for row in rows] == ["e1"]


class TestFacets:
    async def test_sources_are_counted_most_first(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Mixed")
        await _seed_entry(path, "c1", "e1", source="qobuz")
        await _seed_entry(path, "c1", "e2", position=1, source="qobuz")
        await _seed_entry(path, "c1", "e3", position=2, source="localfiles")

        values = await store.sources_of("c1")

        assert [(v.id, v.count) for v in values] == [("qobuz", 2), ("localfiles", 1)]

    async def test_a_genre_spelled_two_ways_keeps_the_commoner_one(
        self, store, tmp_path
    ):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "Mixed")
        await _seed_entry(path, "c1", "e1", genres=[("jazz", "Jazz")])
        await _seed_entry(path, "c1", "e2", position=1, genres=[("jazz", "Jazz")])
        await _seed_entry(path, "c1", "e3", position=2, genres=[("jazz", "JAZZ")])

        values = await store.genres_of("c1")

        assert [(v.id, v.name, v.count) for v in values] == [("jazz", "Jazz", 3)]

    async def test_sources_come_for_a_whole_page_at_once(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "One")
        await _seed_collection(path, "c2", "Two")
        await _seed_collection(path, "c3", "Empty")
        await _seed_entry(path, "c1", "e1", source="qobuz")
        await _seed_entry(path, "c1", "e2", position=1, source="localfiles")
        await _seed_entry(path, "c1", "e3", position=2, source="localfiles")
        await _seed_entry(path, "c2", "e4", source="jamendo")

        by_collection = await store.sources_by_collection(["c1", "c2", "c3"])

        assert by_collection == {"c1": ["localfiles", "qobuz"], "c2": ["jamendo"]}
        assert await store.sources_by_collection([]) == {}

    async def test_facets_are_per_collection(self, store, tmp_path):
        path = str(tmp_path / "collections.db")
        await _seed_collection(path, "c1", "One")
        await _seed_collection(path, "c2", "Two")
        await _seed_entry(path, "c1", "e1", source="qobuz", genres=[("jazz", "Jazz")])
        await _seed_entry(path, "c2", "e2", source="jamendo", genres=[("rock", "Rock")])

        assert [v.id for v in await store.sources_of("c1")] == ["qobuz"]
        assert [v.id for v in await store.genres_of("c1")] == ["jazz"]


async def test_reopening_keeps_what_was_stored(tmp_path):
    path = str(tmp_path / "collections.db")
    store = CollectionStore(path)
    await store.open()
    await _seed_collection(path, "c1", "Kept")
    await store.close()

    store = CollectionStore(path)
    await store.open()
    try:
        assert (await store.get_collection("c1")) is not None
    finally:
        await store.close()


async def test_using_the_store_before_it_is_open_says_so(tmp_path):
    store = CollectionStore(str(tmp_path / "collections.db"))
    with pytest.raises(RuntimeError):
        await store.list_collections()


async def test_a_file_that_cannot_be_opened_leaves_the_store_empty(tmp_path):
    """A collections file the server cannot read costs it collections, never
    its startup."""
    unreadable = tmp_path / "collections.db"
    unreadable.write_text("this is not a database")
    store = CollectionStore(str(unreadable))

    await store.open()

    assert not store.is_good()
    assert await store.list_collections() == ([], 0)
    assert await store.get_collection("c1") is None
    assert await store.list_entries("c1") == ([], 0)
    assert await store.sources_of("c1") == []
    assert await store.genres_of("c1") == []
