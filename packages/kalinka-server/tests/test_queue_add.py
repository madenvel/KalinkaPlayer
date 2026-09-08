"""What reaches the queue: a container's tracks resolved through the sources
that own them, not through the container's."""

import pytest
from fastapi import HTTPException

from kalinka_plugin_sdk.datamodel import (
    Album,
    BrowseItem,
    BrowseItemList,
    Catalog,
    EntityId,
    EntityType,
    Track,
)
from kalinka_plugin_sdk.inputmodule import TrackInfo

from kalinka_server.queue_add import track_infos_for


def _track_id(source, local):
    return EntityId(id=local, type=EntityType.TRACK, source=source)


def _track_item(source, local):
    tid = _track_id(source, local)
    album = EntityId(id=f"al-{local}", type=EntityType.ALBUM, source=source)
    return BrowseItem(
        id=tid,
        name=local,
        can_add=True,
        track=Track(id=tid, title=local, duration=1, album=Album(id=album, title="")),
    )


def _catalog_item(source, local):
    cid = EntityId(id=local, type=EntityType.CATALOG, source=source)
    return BrowseItem(id=cid, name=local, can_browse=True, catalog=Catalog(id=cid, title=local))


class _Container:
    """A browse source holding whatever it was given, from any source."""

    def __init__(self, items):
        self._items = items
        self.browsed = []

    async def browse(self, entity_id, offset=0, limit=50, filter=None):
        self.browsed.append((entity_id.to_string, limit))
        return BrowseItemList(
            offset=offset, limit=limit, total=len(self._items), items=self._items
        )


class _Module:
    """One source's lookups, recording what it was asked for."""

    def __init__(self, name, missing=()):
        self._name = name
        self._missing = set(missing)
        self.asked = []

    async def get_track_info(self, track_ids):
        self.asked.append(list(track_ids))
        return [
            TrackInfo(
                id=_track_id(self._name, local),
                source_retriever=lambda: None,
                metadata=None,
            )
            for local in track_ids
            if local not in self._missing
        ]


def _resolvers(container, modules):
    def browse_source_for(entity_id):
        return container

    def module_for(name):
        if name not in modules:
            raise HTTPException(status_code=404, detail=f"'{name}' is not available")
        return modules[name]

    return browse_source_for, module_for


async def test_each_source_is_asked_for_its_own_ids_and_the_order_holds():
    container = _Container(
        [
            _track_item("qobuz", "q1"),
            _track_item("localfiles", "l1"),
            _track_item("qobuz", "q2"),
            _track_item("jamendo", "j1"),
        ]
    )
    modules = {
        "qobuz": _Module("qobuz"),
        "localfiles": _Module("localfiles"),
        "jamendo": _Module("jamendo"),
    }
    browse_source_for, module_for = _resolvers(container, modules)

    tracks = await track_infos_for(
        ["kalinka:collections:playlist:c1"], browse_source_for, module_for
    )

    assert [track.id.to_string for track in tracks] == [
        "kalinka:qobuz:track:q1",
        "kalinka:localfiles:track:l1",
        "kalinka:qobuz:track:q2",
        "kalinka:jamendo:track:j1",
    ]
    assert modules["qobuz"].asked == [["q1", "q2"]]
    assert modules["localfiles"].asked == [["l1"]]


async def test_a_track_id_needs_no_browse():
    container = _Container([])
    modules = {"qobuz": _Module("qobuz")}
    browse_source_for, module_for = _resolvers(container, modules)

    tracks = await track_infos_for(
        ["kalinka:qobuz:track:q1"], browse_source_for, module_for
    )

    assert [track.id.id for track in tracks] == ["q1"]
    assert container.browsed == []


async def test_a_track_asked_for_twice_is_looked_up_once_and_added_twice():
    container = _Container([_track_item("qobuz", "q1"), _track_item("qobuz", "q1")])
    modules = {"qobuz": _Module("qobuz")}
    browse_source_for, module_for = _resolvers(container, modules)

    tracks = await track_infos_for(
        ["kalinka:collections:playlist:c1"], browse_source_for, module_for
    )

    assert len(tracks) == 2
    assert modules["qobuz"].asked == [["q1"]]


async def test_a_track_a_source_does_not_return_is_left_out():
    container = _Container([_track_item("qobuz", "q1"), _track_item("qobuz", "gone")])
    modules = {"qobuz": _Module("qobuz", missing={"gone"})}
    browse_source_for, module_for = _resolvers(container, modules)

    tracks = await track_infos_for(
        ["kalinka:collections:playlist:c1"], browse_source_for, module_for
    )

    assert [track.id.id for track in tracks] == ["q1"]


async def test_children_that_are_not_tracks_are_skipped():
    container = _Container([_catalog_item("qobuz", "sub"), _track_item("qobuz", "q1")])
    modules = {"qobuz": _Module("qobuz")}
    browse_source_for, module_for = _resolvers(container, modules)

    tracks = await track_infos_for(
        ["kalinka:qobuz:album:a1"], browse_source_for, module_for
    )

    assert [track.id.id for track in tracks] == ["q1"]


async def test_a_source_that_cannot_be_reached_fails_the_add():
    container = _Container([_track_item("qobuz", "q1")])
    browse_source_for, module_for = _resolvers(container, {})

    with pytest.raises(HTTPException):
        await track_infos_for(
            ["kalinka:collections:playlist:c1"], browse_source_for, module_for
        )


async def test_containers_are_taken_in_one_page():
    container = _Container([_track_item("qobuz", "q1")])
    modules = {"qobuz": _Module("qobuz")}
    browse_source_for, module_for = _resolvers(container, modules)

    await track_infos_for(["kalinka:qobuz:album:a1"], browse_source_for, module_for)

    assert container.browsed == [("kalinka:qobuz:album:a1", 5000)]
