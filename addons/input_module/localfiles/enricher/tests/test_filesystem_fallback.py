#!/usr/bin/env python3
"""
Test script to verify the filesystem fallback plugin.
"""

import sys
import os
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Import the plugin and config
from addons.input_module.localfiles.enricher.filesystem_fallback_plugin import (
    FilesystemFallbackPlugin,
)
from addons.input_module.localfiles.config_model import LocalFilesConfig


def test_filesystem_fallback_fix():
    """Test that the plugin respects music folder boundaries."""

    # Create a test config with specific music folders
    config = LocalFilesConfig(
        music_folders=["/home/user/Music", "/media/external/Audio"]
    )

    # Create plugin instance (without db_manager for this test)
    plugin = FilesystemFallbackPlugin(config, None)

    # Test cases
    test_cases = [
        {
            "name": "File within music folder - 2 levels",
            "path": "/home/user/Music/Album/track.mp3",
            "expected_album": "Album",
            "expected_artist": None,
        },
        {
            "name": "File within music folder - 3 levels",
            "path": "/home/user/Music/Artist/Album/track.mp3",
            "expected_album": "Album",
            "expected_artist": "Artist",
        },
        {
            "name": "File outside music folders",
            "path": "/home/user/Downloads/Music/Artist/Album/track.mp3",
            "expected_album": None,
            "expected_artist": None,
        },
        {
            "name": "File with artist-album folder format",
            "path": "/media/external/Audio/Artist - Album/track.mp3",
            "expected_album": "Album",
            "expected_artist": "Artist",
        },
        {
            "name": "Deep nested file within music folder",
            "path": "/home/user/Music/Genre/Artist/Album/track.mp3",
            "expected_album": "Album",
            "expected_artist": "Artist",
        },
        {
            "name": "Deep nested file within music folder",
            "path": "/home/user/Music/track.mp3",
            "expected_album": None,
            "expected_artist": None,
        },
    ]

    print("Testing filesystem fallback plugin fix...")
    print("=" * 50)

    for test_case in test_cases:
        print(f"\nTest: {test_case['name']}")
        print(f"Path: {test_case['path']}")

        # Test if music folder is found correctly
        containing_folder = plugin._find_containing_music_folder(test_case["path"])
        print(f"Containing music folder: {containing_folder}")

        # Extract metadata
        metadata = plugin._extract_metadata_from_path(test_case["path"])
        print(f"Extracted metadata: {metadata}")

        # Check expectations
        album_match = metadata.get("album") == test_case["expected_album"]
        artist_match = metadata.get("artist") == test_case["expected_artist"]

        if album_match and artist_match:
            print("✓ PASS")
        else:
            print("✗ FAIL")
            print(
                f"  Expected album: {test_case['expected_album']}, got: {metadata.get('album')}"
            )
            print(
                f"  Expected artist: {test_case['expected_artist']}, got: {metadata.get('artist')}"
            )


if __name__ == "__main__":
    test_filesystem_fallback_fix()
