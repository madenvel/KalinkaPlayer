"""Tests for server-generated catalog-card background art.

Covers the pure renderer (both variants, determinism, fingerprinting) and the
service layer: inline decoration never blocks, files carry a content-derived
name, unchanged inputs reuse the file, and the served path is validated.
"""

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
)

from kalinka_server import catalog_art_render as render
from kalinka_server.catalog_art_service import ART_URL_PREFIX, CatalogArtService


# --------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------


def _solid_cover(color, size=120):
    return Image.new("RGB", (size, size), color)


def test_render_covers_variant_is_deterministic():
    covers = [_solid_cover((200, 40, 40)), _solid_cover((40, 200, 40))]
    a = render.render_catalog_art(covers, [], "seed:x", width=320, height=180)
    b = render.render_catalog_art(covers, [], "seed:x", width=320, height=180)
    assert a.size == (320, 180)
    assert a.tobytes() == b.tobytes()


def test_render_textual_variant_differs_by_names():
    base = render.render_catalog_art([], ["Rock", "Jazz"], "seed:y", width=320, height=180)
    other = render.render_catalog_art([], ["Pop", "Folk"], "seed:y", width=320, height=180)
    assert base.tobytes() != other.tobytes()


def test_render_empty_is_still_dark_and_valid():
    img = render.render_catalog_art([], [], "seed:z", width=200, height=120)
    assert img.mode == "RGB"
    # A dark-by-design backdrop: mean luminance stays low.
    import numpy as np

    mean = float(np.asarray(img.resize((20, 12)), dtype=np.float32).mean())
    assert mean < 90


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


def _service(tmp_path, resolver=lambda eid: None):
    return CatalogArtService(str(tmp_path / "catalog_art"), resolver)


def test_decorate_enqueues_and_does_not_block(tmp_path):
    svc = _service(tmp_path)
    result = BrowseItemList(offset=0, limit=10, total=1, items=[_catalog_item("albums")])
    svc.decorate(result)
    # No file yet → image stays empty, but a job is queued.
    assert result.items[0].catalog.image is None
    assert svc._queue.qsize() == 1


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

    async def browse(self, entity_id, offset=0, limit=10, genre_ids=None):
        return BrowseItemList(
            offset=0, limit=limit, total=len(self._children), items=self._children
        )

    async def get_resource_path(self, resource):
        return self._covers.get(resource)


async def test_process_generates_cover_variant(tmp_path):
    # Two album children with on-disk covers.
    covers = {}
    for local in ("a1", "a2"):
        blob = render.encode_jpeg(_solid_cover((200, 50, 50)))
        path = tmp_path / f"{local}.jpg"
        path.write_bytes(blob)
        covers[f"album/{local}_large.jpg"] = str(path)

    children = [_album_child("a1"), _album_child("a2")]
    module = _FakeModule(children, covers)
    svc = _service(tmp_path, resolver=lambda eid: module)

    cat_id = "kalinka:localfiles:catalog:albums"
    await svc._process(cat_id, textual=False)

    entry = svc._entries[cat_id]
    assert entry["file"]
    assert (svc._dir / entry["file"]).is_file()
    # Served name validates and re-fetch with unchanged inputs reuses the file.
    assert svc.art_file(entry["file"]) is not None
    first_file = entry["file"]
    await svc._process(cat_id, textual=False)
    assert svc._entries[cat_id]["file"] == first_file


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
    await svc._process(cat_id, textual=True)
    assert (svc._dir / svc._entries[cat_id]["file"]).is_file()


async def test_process_empty_page_records_retry(tmp_path):
    module = _FakeModule([])
    svc = _service(tmp_path, resolver=lambda eid: module)
    cat_id = "kalinka:localfiles:catalog:albums"
    await svc._process(cat_id, textual=False)
    assert svc._entries[cat_id]["next_check_at"] > 0
    assert "file" not in svc._entries[cat_id]


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
