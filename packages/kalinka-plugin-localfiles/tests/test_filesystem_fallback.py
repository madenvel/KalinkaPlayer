#!/usr/bin/env python3
"""
Test script to verify the filesystem fallback plugin.
"""

import pytest

# Import the plugin and config
from kalinka_plugin_localfiles.enricher.filesystem_fallback_plugin import (
    FilesystemFallbackPlugin,
)
from kalinka_plugin_localfiles.config_model import LocalFilesConfig


@pytest.fixture
def config():
    """Create a test config with specific music folders."""
    return LocalFilesConfig(music_folders=["/home/user/Music", "/media/external/Audio"])


@pytest.fixture
def plugin(config):
    """Create plugin instance (without db_manager for this test)."""
    return FilesystemFallbackPlugin(config, None)


@pytest.mark.parametrize(
    "path,expected_album,expected_artist",
    [
        (
            "/home/user/Music/Album/track.mp3",
            "Album",
            None,
        ),
        (
            "/home/user/Music/Artist/Album/track.mp3",
            "Album",
            "Artist",
        ),
        (
            "/home/user/Downloads/Music/Artist/Album/track.mp3",
            None,
            None,
        ),
        (
            "/media/external/Audio/Artist - Album/track.mp3",
            "Album",
            "Artist",
        ),
        (
            "/home/user/Music/Genre/Artist/Album/track.mp3",
            "Album",
            "Artist",
        ),
        (
            "/home/user/Music/track.mp3",
            None,
            None,
        ),
    ],
)
def test_filesystem_fallback_metadata_extraction(
    plugin, path, expected_album, expected_artist
):
    """Test that the plugin respects music folder boundaries and extracts metadata correctly."""

    # Test if music folder is found correctly
    containing_folder = plugin._find_containing_music_folder(path)

    # Extract metadata
    metadata = plugin._extract_metadata_from_path(path)

    # Assert expectations
    assert (
        metadata.get("album") == expected_album
    ), f"Expected album '{expected_album}', got '{metadata.get('album')}' for path {path}"
    assert (
        metadata.get("artist") == expected_artist
    ), f"Expected artist '{expected_artist}', got '{metadata.get('artist')}' for path {path}"


class TestFilesystemFallbackPlugin:
    """Test class for FilesystemFallbackPlugin functionality."""

    def test_find_containing_music_folder_within_bounds(self, plugin):
        """Test finding containing music folder for files within configured folders."""
        path = "/home/user/Music/Artist/Album/track.mp3"
        containing_folder = plugin._find_containing_music_folder(path)
        assert containing_folder == "/home/user/Music"

    def test_find_containing_music_folder_outside_bounds(self, plugin):
        """Test finding containing music folder for files outside configured folders."""
        path = "/home/user/Downloads/Music/Artist/Album/track.mp3"
        containing_folder = plugin._find_containing_music_folder(path)
        assert containing_folder is None

    def test_metadata_extraction_two_levels_deep(self, plugin):
        """Test metadata extraction for files two levels deep in music folder."""
        path = "/home/user/Music/Album/track.mp3"
        metadata = plugin._extract_metadata_from_path(path)
        assert metadata.get("album") == "Album"
        assert metadata.get("artist") is None

    def test_metadata_extraction_three_levels_deep(self, plugin):
        """Test metadata extraction for files three levels deep in music folder."""
        path = "/home/user/Music/Artist/Album/track.mp3"
        metadata = plugin._extract_metadata_from_path(path)
        assert metadata.get("album") == "Album"
        assert metadata.get("artist") == "Artist"

    def test_metadata_extraction_artist_album_format(self, plugin):
        """Test metadata extraction for artist-album folder format."""
        path = "/media/external/Audio/Artist - Album/track.mp3"
        metadata = plugin._extract_metadata_from_path(path)
        assert metadata.get("album") == "Album"
        assert metadata.get("artist") == "Artist"

    def test_metadata_extraction_root_level_file(self, plugin):
        """Test metadata extraction for files directly in music folder root."""
        path = "/home/user/Music/track.mp3"
        metadata = plugin._extract_metadata_from_path(path)
        assert metadata.get("album") is None
        assert metadata.get("artist") is None


class TestNoSpaceDotPrefix:
    """Filenames shaped N.Title (no space after the dot) parse number + clean
    title, and a stem-echo title (basename minus extension) still counts as a
    filename placeholder so a retry can improve it."""

    def test_extract_number_and_title_no_space(self, plugin):
        num, title, artist = plugin._extract_track_number_and_title(
            "1.Кончится лето"
        )
        assert num == 1
        assert title == "Кончится лето"
        assert artist is None

    def test_year_prefix_not_a_track_number(self, plugin):
        num, title, _ = plugin._extract_track_number_and_title("1985.Some Song")
        assert num is None
        assert title == "1985.Some Song"

    @pytest.mark.asyncio
    async def test_stem_echo_title_gets_replaced(self, plugin):
        track = {
            "id": "t1",
            "title": "1.Кончится лето",  # stem of the file below
            "file_path": "/home/user/Music/В.Цой - Черный альбом/1.Кончится лето.flac",
            "artist_id": "artist_x",
            "album_id": "album_x",
            "track_number": None,
        }
        result = await plugin.enrich_track(track)
        assert result is not None
        updates = result["updates"]
        assert updates["title"] == "Кончится лето"   # prefix stripped
        assert updates["track_number"] == 1


class TestTrackNumberAfterArtistPrefix:
    """A track number after the "Artist - " prefix ("Artist - 01.Title")
    is still parsed — the number sits mid-filename, not at the start."""

    def test_number_after_artist_prefix(self, plugin):
        num, title, artist = plugin._extract_track_number_and_title(
            "THE BEATLES - 01.Come Together (Lennon-McCartney)"
        )
        assert num == 1
        assert title == "Come Together (Lennon-McCartney)"
        assert artist == "THE BEATLES"

    def test_no_false_number_when_title_has_no_prefix(self, plugin):
        num, title, artist = plugin._extract_track_number_and_title(
            "Pink Floyd - Time"
        )
        assert num is None
        assert title == "Time"
        assert artist == "Pink Floyd"
