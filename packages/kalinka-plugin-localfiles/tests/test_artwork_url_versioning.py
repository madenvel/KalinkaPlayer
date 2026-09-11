#!/usr/bin/env python3
"""Artwork URLs that change when the artwork does.

The files are named after the entity, so a cover that gets replaced keeps the
URL it had. Anything caching by URL — a browser, a CDN, Flutter's in-memory
image cache — then has no way to learn the bytes moved, and goes on showing
the cover it fetched first. Carrying the write time in the query string gives
the cache a new key while leaving the server the same path to resolve.
"""

import os

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


def _write_art(module, entity_type, base, mtime=None):
    """One saved image set, optionally stamped with a chosen write time."""
    folder = module.artwork_path / entity_type
    folder.mkdir(parents=True, exist_ok=True)
    for suffix in ("thumbnail", "small", "large"):
        path = folder / f"{base}_{suffix}.jpg"
        Image.new("RGB", (10, 10), (4, 5, 6)).save(path, "JPEG")
        if mtime is not None:
            os.utime(path, (mtime, mtime))


class TestTheVersion:
    def test_the_url_carries_the_write_time(self, module):
        _write_art(module, "album", "album_1", mtime=1_700_000_000)
        urls = module._resource_image_urls("album", "album_1.jpg")
        assert urls.large == "/resource/album/album_1_large.jpg?v=1700000000"

    def test_every_size_carries_the_same_version(self, module):
        _write_art(module, "album", "album_1", mtime=1_700_000_000)
        urls = module._resource_image_urls("album", "album_1.jpg")
        versions = {
            u.split("?v=")[1] for u in (urls.thumbnail, urls.small, urls.large)
        }
        assert versions == {"1700000000"}

    def test_replacing_the_artwork_changes_the_url(self, module):
        """The whole point: same file name, different bytes, new URL."""
        _write_art(module, "album", "album_1", mtime=1_700_000_000)
        before = module._resource_image_urls("album", "album_1.jpg")
        _write_art(module, "album", "album_1", mtime=1_700_000_500)
        after = module._resource_image_urls("album", "album_1.jpg")

        assert before.large != after.large

    def test_only_the_query_changes_so_the_file_still_resolves(self, module):
        """``/resource/`` matches on the path alone, so the version must not
        move into it or the server would look for a file that isn't there."""
        _write_art(module, "album", "album_1", mtime=1_700_000_000)
        before = module._resource_image_urls("album", "album_1.jpg")
        _write_art(module, "album", "album_1", mtime=1_700_000_500)
        after = module._resource_image_urls("album", "album_1.jpg")

        assert before.large.split("?")[0] == after.large.split("?")[0]

    def test_the_path_names_a_file_that_exists(self, module):
        _write_art(module, "album", "album_1")
        urls = module._resource_image_urls("album", "album_1.jpg")
        path, _, query = urls.thumbnail.partition("?")

        assert query.startswith("v=")
        assert (module.artwork_path / path.removeprefix("/resource/")).is_file()

    def test_an_untouched_cover_keeps_its_url(self, module):
        """A stable URL is still wanted where nothing changed; only a real
        rewrite should cost clients a refetch."""
        _write_art(module, "album", "album_1", mtime=1_700_000_000)
        first = module._resource_image_urls("album", "album_1.jpg")
        second = module._resource_image_urls("album", "album_1.jpg")

        assert first.large == second.large


class TestEveryEntityType:
    @pytest.mark.parametrize("entity_type", ["album", "artist", "track", "playlist"])
    def test_the_version_is_not_album_specific(self, module, entity_type):
        _write_art(module, entity_type, f"{entity_type}_1", mtime=1_700_000_000)
        urls = module._resource_image_urls(entity_type, f"{entity_type}_1.jpg")

        assert urls.small == (
            f"/resource/{entity_type}/{entity_type}_1_small.jpg?v=1700000000"
        )


class TestWhenThereIsNoImage:
    def test_no_image_url_gives_nothing(self, module):
        assert module._resource_image_urls("album", None) is None
        assert module._resource_image_urls("album", "") is None

    def test_a_recorded_image_with_no_file_gives_nothing(self, module):
        """The row can name a cover the artwork folder never received."""
        assert module._resource_image_urls("album", "album_missing.jpg") is None
