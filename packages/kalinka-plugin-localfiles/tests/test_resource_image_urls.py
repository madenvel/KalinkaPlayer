#!/usr/bin/env python3
"""The ``/resource/`` links a browse item carries for a saved image set.

A link names a path and nothing else, so anything that resolves it as a file
name — the app, the server's own catalog-art composer — finds the file. The
cost is that a cover replaced under the same name keeps its link, and a client
holding it cached shows the old picture until it drops it. Where a moving link
is wanted the art is content-named instead, as ``catalog_art_service`` does.
"""

import pytest
from PIL import Image

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.localfiles import LocalFilesInputModule


class _FakeDb:
    def is_good(self):
        return True


@pytest.fixture
def module(tmp_path):
    config = LocalFilesConfig(
        music_folders=[str(tmp_path / "music")],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
    )
    return LocalFilesInputModule(config, _FakeDb())


def _write_art(module, entity_type, base):
    folder = module.artwork_path / entity_type
    folder.mkdir(parents=True, exist_ok=True)
    for suffix in ("thumbnail", "small", "large"):
        Image.new("RGB", (10, 10), (4, 5, 6)).save(
            folder / f"{base}_{suffix}.jpg", "JPEG"
        )


class TestTheLink:
    def test_every_size_is_offered(self, module):
        _write_art(module, "album", "album_1")
        urls = module._resource_image_urls("album", "album_1.jpg")

        assert (urls.thumbnail, urls.small, urls.large) == (
            "/resource/album/album_1_thumbnail.jpg",
            "/resource/album/album_1_small.jpg",
            "/resource/album/album_1_large.jpg",
        )

    def test_it_carries_no_query(self, module):
        """A query is read as part of the name by anything resolving the link
        as a file, which is how the catalog-art composer reads it."""
        _write_art(module, "album", "album_1")
        urls = module._resource_image_urls("album", "album_1.jpg")

        assert "?" not in urls.thumbnail + urls.small + urls.large

    def test_the_path_names_a_file_that_exists(self, module):
        _write_art(module, "album", "album_1")
        urls = module._resource_image_urls("album", "album_1.jpg")

        assert (
            module.artwork_path / urls.thumbnail.removeprefix("/resource/")
        ).is_file()

    @pytest.mark.parametrize("entity_type", ["album", "artist", "track", "playlist"])
    def test_every_entity_type_is_linked_alike(self, module, entity_type):
        _write_art(module, entity_type, f"{entity_type}_1")
        urls = module._resource_image_urls(entity_type, f"{entity_type}_1.jpg")

        assert urls.small == f"/resource/{entity_type}/{entity_type}_1_small.jpg"


class TestWhenThereIsNoImage:
    def test_no_image_url_gives_nothing(self, module):
        assert module._resource_image_urls("album", None) is None
        assert module._resource_image_urls("album", "") is None

    def test_a_recorded_image_with_no_file_gives_nothing(self, module):
        """The row can name a cover the artwork folder never received."""
        assert module._resource_image_urls("album", "album_missing.jpg") is None
