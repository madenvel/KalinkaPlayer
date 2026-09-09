"""Tests for the browse-item art renderers and the generation service."""

from __future__ import annotations

import io

from PIL import Image

from kalinka_plugin_sdk.datamodel import (
    Album,
    BrowseItem,
    BrowseItemList,
    Catalog,
    CoverImage,
    EntityId,
    EntityType,
    Owner,
    Playlist,
    Track,
)

from kalinka_server import catalog_art_render as render
from kalinka_server.catalog_art_service import ART_URL_PREFIX, CatalogArtService


# --------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------


def _solid_cover(color, size=120):
    return Image.new("RGB", (size, size), color)


def test_render_card_with_covers_is_deterministic():
    covers = [_solid_cover((200, 40, 40)), _solid_cover((40, 200, 40))]
    a = render.render_catalog_art(covers, [], "seed:x", width=320, height=180)
    b = render.render_catalog_art(covers, [], "seed:x", width=320, height=180)
    assert a.size == (320, 180)
    assert a.mode == "RGB"  # opaque, full-bleed tile
    assert a.tobytes() == b.tobytes()


def test_render_coverless_tile_varies_by_seed():
    # No covers -> a background-only tile; its key hue is seeded from the id, so
    # different catalogs don't all look the same.
    rock = render.render_catalog_art([], [], "cat:rock", width=320, height=180)
    jazz = render.render_catalog_art([], [], "cat:jazz", width=320, height=180)
    assert rock.mode == "RGB"
    assert rock.tobytes() != jazz.tobytes()


def test_render_is_dark_by_design():
    import numpy as np

    img = render.render_catalog_art([], [], "seed:z", width=200, height=120)
    mean = float(np.asarray(img.resize((20, 12)), dtype=np.float32).mean())
    assert mean < 120  # dark backdrop overall


def test_encode_jpeg_roundtrips():
    img = render.render_catalog_art([_solid_cover((80, 80, 200))], [], "s", width=160, height=90)
    data = render.encode_jpeg(img)
    assert data[:2] == b"\xff\xd8"  # JPEG SOI
    Image.open(io.BytesIO(data)).verify()


def test_fingerprint_changes_with_style_version(monkeypatch):
    fp1 = render.content_fingerprint([b"a"], ["N"], "seed")
    monkeypatch.setattr(render, "STYLE_VERSION", render.STYLE_VERSION + 1)
    fp2 = render.content_fingerprint([b"a"], ["N"], "seed")
    assert fp1 != fp2


def test_fingerprint_tells_the_two_shapes_apart():
    card = render.content_fingerprint([b"a"], [], "seed", render.ArtStyle.CARD)
    cover = render.content_fingerprint([b"a"], [], "seed", render.ArtStyle.COVER)
    assert card != cover


def test_playlist_cover_mosaics_four_albums():
    colors = [(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30)]
    cover = render.render_playlist_cover([_solid_cover(c) for c in colors], side=200)

    assert cover.size == (200, 200)
    quadrants = [(50, 50), (150, 50), (50, 150), (150, 150)]
    assert [cover.getpixel(point) for point in quadrants] == colors


def test_playlist_cover_takes_one_album_below_four():
    covers = [_solid_cover((200, 30, 30)), _solid_cover((30, 200, 30))]
    cover = render.render_playlist_cover(covers, side=200)

    assert cover.size == (200, 200)
    # The whole square is the first album — no half-filled grid.
    assert cover.getpixel((50, 50)) == cover.getpixel((150, 150)) == (200, 30, 30)


def test_playlist_cover_squares_an_oblong_album():
    cover = render.render_playlist_cover(
        [Image.new("RGB", (400, 100), (10, 20, 30))], side=120
    )
    assert cover.size == (120, 120)


# --------------------------------------------------------------------------
# Service — inline decoration
# --------------------------------------------------------------------------


def _catalog_item(local, *, image=None, preview=None):
    eid = EntityId(id=local, type=EntityType.CATALOG, source="localfiles")
    return BrowseItem(
        id=eid,
        name=local.title(),
        can_browse=True,
        catalog=Catalog(id=eid, title=local.title(), image=image, preview_config=preview),
    )


def _album_child(local, *, with_image=True):
    eid = EntityId(id=local, type=EntityType.ALBUM, source="localfiles")
    image = (
        CoverImage(
            large=f"/resource/album/{local}_large.jpg",
            small=f"/resource/album/{local}_small.jpg",
            thumbnail=f"/resource/album/{local}_thumb.jpg",
        )
        if with_image
        else None
    )
    return BrowseItem(
        id=eid, name=local, can_browse=True, album=Album(id=eid, title=local, image=image)
    )


def _service(tmp_path, resolver=lambda eid: None, resource_resolver=None):
    """The fake plays both roles unless a test wants them apart — a source
    that browses need not be one that can serve a file."""
    return CatalogArtService(
        str(tmp_path / "catalog_art"), resolver, resource_resolver or resolver
    )


def test_decorate_enqueues_and_does_not_block(tmp_path):
    svc = _service(tmp_path)
    result = BrowseItemList(offset=0, limit=10, total=1, items=[_catalog_item("albums")])
    svc.decorate(result)
    # No file yet → image stays empty, but a job is queued.
    assert result.items[0].catalog.image is None
    assert svc._queue.qsize() == 1


def _collection_item(local, *, track_count):
    """A collection as the collections source lists it: one id, a playlist
    payload for rows and a catalog payload for the page."""
    eid = EntityId(id=local, type=EntityType.PLAYLIST, source="collections")
    owner = Owner(name="You", id=EntityId(id="you", type=EntityType.USER, source="collections"))
    return BrowseItem(
        id=eid,
        name=local,
        can_browse=True,
        can_add=True,
        can_edit=True,
        playlist=Playlist(
            id=eid, name=local, owner=owner, description="", track_count=track_count
        ),
        catalog=Catalog(id=eid, title=local),
    )


def test_a_collection_with_tracks_carries_its_art_on_both_payloads(tmp_path):
    svc = _service(tmp_path)
    item = _collection_item("c1", track_count=3)
    svc._entries[item.id.to_string] = {"file": "abc.jpg", "next_check_at": 1e12}

    svc.decorate(BrowseItemList(offset=0, limit=10, total=1, items=[item]))

    assert item.catalog.image is not None
    assert item.playlist.image == item.catalog.image


def test_a_collection_asks_for_a_cover_and_a_catalog_for_a_card(tmp_path):
    svc = _service(tmp_path)
    items = [_collection_item("c1", track_count=3), _catalog_item("albums")]

    svc.decorate(BrowseItemList(offset=0, limit=10, total=2, items=items))

    queued = {}
    while not svc._queue.empty():
        cat_id, style, _ = svc._queue.get_nowait()
        queued[cat_id] = style
    assert queued == {
        items[0].id.to_string: render.ArtStyle.COVER,
        items[1].id.to_string: render.ArtStyle.CARD,
    }


def test_an_empty_collection_gets_no_art_and_asks_for_none(tmp_path):
    svc = _service(tmp_path)
    item = _collection_item("c1", track_count=0)
    svc._entries[item.id.to_string] = {"file": "abc.jpg", "next_check_at": 0.0}

    svc.decorate(BrowseItemList(offset=0, limit=10, total=1, items=[item]))

    assert item.catalog.image is None
    assert item.playlist.image is None
    assert svc._queue.empty()


def test_decorate_skips_items_with_own_image(tmp_path):
    svc = _service(tmp_path)
    img = CoverImage(large="/resource/x.jpg")
    result = BrowseItemList(
        offset=0, limit=10, total=1, items=[_catalog_item("albums", image=img)]
    )
    svc.decorate(result)
    assert svc._queue.qsize() == 0


def test_decorate_fills_url_once_generated(tmp_path):
    svc = _service(tmp_path)
    cat_id = "kalinka:localfiles:catalog:albums"
    svc._entries[cat_id] = {
        "file": "abcdef0123456789-0a1b2c3d.jpg",
        "fingerprint": "x",
        "next_check_at": 1e18,  # far future → no re-enqueue
    }
    result = BrowseItemList(offset=0, limit=10, total=1, items=[_catalog_item("albums")])
    svc.decorate(result)
    url = result.items[0].catalog.image.large
    assert url == f"{ART_URL_PREFIX}/abcdef0123456789-0a1b2c3d.jpg"
    assert svc._queue.qsize() == 0


def test_art_file_rejects_bad_names(tmp_path):
    svc = _service(tmp_path)
    assert svc.art_file("../../etc/passwd") is None
    assert svc.art_file("not-a-fingerprint.jpg") is None
    assert svc.art_file("abcdef0123456789-0a1b2c3d.png") is None


# --------------------------------------------------------------------------
# Service — generation worker
# --------------------------------------------------------------------------


class _FakeModule:
    def __init__(self, children, cover_bytes_by_resource=None):
        self._children = children
        self._covers = cover_bytes_by_resource or {}

    async def browse(self, entity_id, offset=0, limit=10, filter=None):
        return BrowseItemList(
            offset=0, limit=limit, total=len(self._children), items=self._children
        )

    async def get_resource_path(self, resource):
        return self._covers.get(resource)


def _covers_on_disk(tmp_path, *locals_, color=(200, 50, 50)):
    """Album covers a resource resolver can hand back, keyed the way the child
    items reference them."""
    covers = {}
    for local in locals_:
        path = tmp_path / f"{local}.jpg"
        path.write_bytes(render.encode_jpeg(_solid_cover(color)))
        covers[f"album/{local}_large.jpg"] = str(path)
    return covers


async def test_process_generates_a_card_with_covers(tmp_path):
    covers = _covers_on_disk(tmp_path, "a1", "a2")
    children = [_album_child("a1"), _album_child("a2")]
    module = _FakeModule(children, covers)
    svc = _service(tmp_path, resolver=lambda eid: module)

    cat_id = "kalinka:localfiles:catalog:albums"
    await svc._process(cat_id, render.ArtStyle.CARD, textual=False)

    entry = svc._entries[cat_id]
    assert entry["file"]
    assert (svc._dir / entry["file"]).is_file()
    # Served name validates and re-fetch with unchanged inputs reuses the file.
    assert svc.art_file(entry["file"]) is not None
    first_file = entry["file"]
    await svc._process(cat_id, render.ArtStyle.CARD, textual=False)
    assert svc._entries[cat_id]["file"] == first_file
    # Atomic writes must not leave temp litter behind.
    assert list(svc._dir.glob("*.tmp")) == []


async def test_a_collection_is_rendered_as_a_square_cover(tmp_path):
    names = ("a1", "a2", "a3", "a4")
    module = _FakeModule(
        [_album_child(local) for local in names], _covers_on_disk(tmp_path, *names)
    )
    svc = _service(tmp_path, resolver=lambda eid: module)

    cat_id = "kalinka:collections:playlist:c1"
    await svc._process(cat_id, render.ArtStyle.COVER, textual=False)

    with Image.open(svc._dir / svc._entries[cat_id]["file"]) as image:
        assert image.width == image.height


def _track_child(album_local, track_local):
    """A track as a collection lists it, wearing its album's picture under a
    URL of its own — the shape a source hands back when it stamps the track
    into the query, as Jamendo does."""
    album_id = EntityId(id=album_local, type=EntityType.ALBUM, source="localfiles")
    track_id = EntityId(id=track_local, type=EntityType.TRACK, source="localfiles")
    return BrowseItem(
        id=track_id,
        name=track_local,
        can_browse=False,
        can_add=True,
        track=Track(
            id=track_id,
            title=track_local,
            duration=100,
            album=Album(
                id=album_id,
                title=album_local,
                image=CoverImage(
                    large=f"/resource/album/{album_local}-{track_local}.jpg"
                ),
            ),
        ),
    )


def _album_tracks(tmp_path, resources, album_local, track_locals, color):
    """Tracks of one album, each naming the same picture by its own URL, with
    the bytes written where the resource resolver will find them."""
    blob = render.encode_jpeg(_solid_cover(color))
    children = []
    for track_local in track_locals:
        path = tmp_path / f"{album_local}-{track_local}.jpg"
        path.write_bytes(blob)
        resources[f"album/{album_local}-{track_local}.jpg"] = str(path)
        children.append(_track_child(album_local, track_local))
    return children


def _cover_spy(monkeypatch):
    """Records what each mosaic was composed from."""
    composed: list[list] = []

    def _spy(covers, **kwargs):
        composed.append(list(covers))
        return render.render_playlist_cover(covers, **kwargs)

    monkeypatch.setattr(
        "kalinka_server.catalog_art_service.render_playlist_cover", _spy
    )
    return composed


async def test_a_mosaic_takes_four_distinct_albums_not_the_first_four(
    tmp_path, monkeypatch
):
    resources: dict[str, str] = {}
    children = (
        _album_tracks(tmp_path, resources, "a1", ["t1", "t2", "t3"], (200, 30, 30))
        + _album_tracks(tmp_path, resources, "a2", ["t4"], (30, 200, 30))
        + _album_tracks(tmp_path, resources, "a3", ["t5"], (30, 30, 200))
        + _album_tracks(tmp_path, resources, "a4", ["t6"], (200, 200, 30))
    )
    svc = _service(tmp_path, resolver=lambda eid: _FakeModule(children, resources))
    composed = _cover_spy(monkeypatch)

    await svc._process(
        "kalinka:collections:playlist:c1", render.ArtStyle.COVER, textual=False
    )

    # The first three entries are one album under three URLs; the mosaic
    # reaches past them for four pictures that differ.
    assert len(composed[0]) == 4
    assert len({cover.tobytes() for cover in composed[0]}) == 4


async def test_one_album_under_many_urls_takes_the_cover_alone(tmp_path, monkeypatch):
    resources: dict[str, str] = {}
    children = _album_tracks(
        tmp_path, resources, "a1", ["t1", "t2", "t3", "t4"], (200, 30, 30)
    )
    svc = _service(tmp_path, resolver=lambda eid: _FakeModule(children, resources))
    composed = _cover_spy(monkeypatch)

    await svc._process(
        "kalinka:collections:playlist:c1", render.ArtStyle.COVER, textual=False
    )

    # Nothing to mosaic, so the one picture gets the whole square rather than
    # being tiled four times.
    assert len(composed[0]) == 1


async def test_two_albums_wearing_one_picture_count_once(tmp_path, monkeypatch):
    resources: dict[str, str] = {}
    children = (
        _album_tracks(tmp_path, resources, "a1", ["t1"], (200, 30, 30))
        + _album_tracks(tmp_path, resources, "a2", ["t2"], (200, 30, 30))
        + _album_tracks(tmp_path, resources, "a3", ["t3"], (30, 200, 30))
        + _album_tracks(tmp_path, resources, "a4", ["t4"], (30, 30, 200))
    )
    svc = _service(tmp_path, resolver=lambda eid: _FakeModule(children, resources))
    composed = _cover_spy(monkeypatch)

    await svc._process(
        "kalinka:collections:playlist:c1", render.ArtStyle.COVER, textual=False
    )

    # Four albums, three pictures between them: an id says two covers differ,
    # the bytes say otherwise, and the bytes win.
    assert len(composed[0]) == 3


async def test_a_collection_takes_covers_from_the_sources_that_own_them(tmp_path):
    """Collections browse but own no bytes: a child's cover belongs to whatever
    source that track came from, so it is the resource resolver — not the
    browsed source — that has to serve it."""
    owner = _FakeModule([], _covers_on_disk(tmp_path, "a1"))
    svc = _service(
        tmp_path,
        resolver=lambda eid: _FakeModule([_album_child("a1")]),
        resource_resolver=lambda eid: owner,
    )

    cat_id = "kalinka:collections:playlist:c1"
    await svc._process(cat_id, render.ArtStyle.COVER, textual=False)

    assert svc._entries[cat_id]["file"]


async def test_a_collection_with_no_reachable_cover_ships_nothing(tmp_path):
    """A mosaic is its albums and nothing else: with none of them to hand
    there is no art to make, and the client's own tile stands in."""
    svc = _service(
        tmp_path,
        resolver=lambda eid: _FakeModule([_album_child("a1")]),
        resource_resolver=lambda eid: None,
    )

    cat_id = "kalinka:collections:playlist:c1"
    await svc._process(cat_id, render.ArtStyle.COVER, textual=False)

    assert svc._entries[cat_id].get("file") is None
    assert list(svc._dir.glob("*.jpg")) == []


async def test_process_textual_when_children_are_catalogs(tmp_path):
    def _sub(local):
        eid = EntityId(id=local, type=EntityType.CATALOG, source="localfiles")
        return BrowseItem(
            id=eid, name=local, can_browse=True, catalog=Catalog(id=eid, title=local)
        )

    children = [_sub("rock"), _sub("jazz"), _sub("pop")]
    module = _FakeModule(children)
    svc = _service(tmp_path, resolver=lambda eid: module)
    cat_id = "kalinka:localfiles:catalog:genres"
    await svc._process(cat_id, render.ArtStyle.CARD, textual=True)
    # Cover-less catalogs still get a background-only tile.
    assert (svc._dir / svc._entries[cat_id]["file"]).is_file()


async def test_process_empty_page_still_makes_background_tile(tmp_path):
    # A flaky/empty upstream must not leave the card blank: ship a provisional
    # background-only tile now and retry soon to add covers.
    module = _FakeModule([])
    svc = _service(tmp_path, resolver=lambda eid: module)
    cat_id = "kalinka:localfiles:catalog:albums"
    await svc._process(cat_id, render.ArtStyle.CARD, textual=False)
    entry = svc._entries[cat_id]
    assert entry["file"] and (svc._dir / entry["file"]).is_file()
    assert entry["provisional"] is True


async def test_process_browse_error_still_makes_background_tile(tmp_path):
    class _Boom:
        async def browse(self, *args, **kwargs):
            raise RuntimeError("jamendo down")

    svc = _service(tmp_path, resolver=lambda eid: _Boom())
    cat_id = "kalinka:jamendo:catalog:popular-tracks"
    await svc._process(cat_id, render.ArtStyle.CARD, textual=False)
    entry = svc._entries[cat_id]
    assert entry["file"] and (svc._dir / entry["file"]).is_file()
    assert entry["provisional"] is True


async def test_provisional_tile_upgrades_when_covers_arrive(tmp_path):
    # First pass: no covers -> provisional background tile.
    svc = _service(tmp_path, resolver=lambda eid: _FakeModule([]))
    cat_id = "kalinka:jamendo:catalog:popular-tracks"
    await svc._process(cat_id, render.ArtStyle.CARD, textual=False)
    bg_file = svc._entries[cat_id]["file"]
    assert svc._entries[cat_id]["provisional"] is True

    # Second pass (fresh service on the same dir = a restart) with covers now
    # available -> upgrade, and the old background file is kept so a client still
    # showing it doesn't 404.
    covers = {}
    blob = render.encode_jpeg(_solid_cover((30, 90, 160)))
    for local in ("a1", "a2"):
        path = tmp_path / f"{local}.jpg"
        path.write_bytes(blob)
        covers[f"album/{local}_large.jpg"] = str(path)
    svc2 = _service(
        tmp_path,
        resolver=lambda eid: _FakeModule(
            [_album_child("a1"), _album_child("a2")], covers
        ),
    )
    await svc2._process(cat_id, render.ArtStyle.CARD, textual=False)
    entry = svc2._entries[cat_id]
    assert entry["file"] != bg_file
    assert entry["provisional"] is False
    assert (svc2._dir / bg_file).is_file()


async def test_transient_failure_keeps_existing_full_tile(tmp_path):
    covers = {}
    blob = render.encode_jpeg(_solid_cover((200, 50, 50)))
    for local in ("a1", "a2"):
        path = tmp_path / f"{local}.jpg"
        path.write_bytes(blob)
        covers[f"album/{local}_large.jpg"] = str(path)
    svc = _service(
        tmp_path,
        resolver=lambda eid: _FakeModule(
            [_album_child("a1"), _album_child("a2")], covers
        ),
    )
    cat_id = "kalinka:jamendo:catalog:popular-tracks"
    await svc._process(cat_id, render.ArtStyle.CARD, textual=False)
    full_file = svc._entries[cat_id]["file"]
    assert svc._entries[cat_id]["provisional"] is False

    # Upstream goes flaky (empty) -> keep the good tile, don't downgrade it.
    svc2 = _service(tmp_path, resolver=lambda eid: _FakeModule([]))
    await svc2._process(cat_id, render.ArtStyle.CARD, textual=False)
    assert svc2._entries[cat_id]["file"] == full_file
    assert (svc2._dir / full_file).is_file()


async def test_index_reload_drops_missing_files(tmp_path):
    svc = _service(tmp_path)
    svc._dir.mkdir(parents=True, exist_ok=True)
    svc._entries["kalinka:localfiles:catalog:albums"] = {
        "file": "deadbeefdeadbeef-01020304.jpg",
        "fingerprint": "x",
        "next_check_at": 1e18,
    }
    svc._save_index()
    # File was never written → a fresh load drops the dangling entry.
    reloaded = _service(tmp_path)
    assert "kalinka:localfiles:catalog:albums" not in reloaded._entries


async def test_style_version_bump_forces_recheck(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    svc._dir.mkdir(parents=True, exist_ok=True)
    name = "deadbeefdeadbeef-01020304.jpg"
    (svc._dir / name).write_bytes(b"x")
    svc._entries["kalinka:localfiles:catalog:albums"] = {
        "file": name,
        "fingerprint": "x",
        "next_check_at": 1e18,
    }
    svc._save_index()
    monkeypatch.setattr(
        "kalinka_server.catalog_art_service.STYLE_VERSION",
        render.STYLE_VERSION + 1,
    )
    reloaded = _service(tmp_path)
    entry = reloaded._entries["kalinka:localfiles:catalog:albums"]
    assert entry["next_check_at"] == 0.0
