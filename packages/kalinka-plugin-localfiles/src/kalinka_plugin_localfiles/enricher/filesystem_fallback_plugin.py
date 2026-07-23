import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional

from ..config_model import LocalFilesConfig
from ..utils.name_utils import (
    TRACK_NUMBER_PREFIX_PATTERNS,
    album_folder_for_path,
    clean_display_name,
)
from .enricher_plugin import EnricherPlugin
from .id_generator import generate_artist_id, generate_album_id

logger = logging.getLogger(__name__.split(".")[-1])


class FilesystemFallbackPlugin(EnricherPlugin):
    """
    Fallback enrichment plugin that extracts metadata from file system structure.

    This plugin uses file names and folder structure to assign artist, album and title:
    - Title: filename without extension and optional track number prefix (e.g., "1. " or "2.")
    - Album: second element of the path, may be in format "Artist - Album"
    - Artist: extracted from filename if in "Artist - Track" format (takes precedence),
             otherwise from album folder name if in "Artist - Album" format,
             or from parent folder as fallback
    - Track number: extracted from filename prefix

    Filename artist-track splitting supports various dash variants: -, –, —, with or without spaces.

    **Important**: This plugin only analyzes folder structure within the configured music
    directories. It will not use folder names outside of the music directories to avoid
    incorrect metadata extraction from system paths.

    Enrichment behavior:
    - Artist enrichment: Updates artist name only if current name is "unknown"
    - Album enrichment: Updates album title only if current title is "unknown"
    - Track enrichment: Updates track metadata and creates/finds artists and albums as needed
    - Uses case-insensitive matching to find existing artists and albums
    - Creates new entities if not found, otherwise reuses existing ones

    This plugin does NOT set the "enriched" flag to allow other plugins to re-check the data.
    """

    # v2: no-space "N.Title" prefix pattern + stem-echo title guard.
    ENRICHER_VERSION = 2

    # Match the indexer's V/A detection criteria
    # (``orphan_va_folder_tracks``). Filesystem fallback skips album
    # reassignment when the track's folder meets these — otherwise it
    # would re-anchor V/A-detached tracks to (folder, tag-title)
    # albums anchored to the first-tagged artist, undoing the detach
    # and breaking artist navigation again.
    VA_MIN_DISTINCT_ARTISTS = 4
    VA_MIN_ARTIST_UNIQUENESS = 0.5

    def __init__(self, config: LocalFilesConfig, db_manager):
        self.config = config
        self.db_manager = db_manager

        # Expand user (~) and resolve absolute paths for music folders
        self.music_folders = [
            str(Path(folder).expanduser().resolve()) for folder in config.music_folders
        ]

        # Track-number prefix patterns are shared with the indexer (which now
        # parses them at scan time too) so the two never disagree.
        self.track_number_patterns = TRACK_NUMBER_PREFIX_PATTERNS

        # Per-folder V/A determination, cached for the lifetime of the
        # plugin instance so we don't re-query the DB for every track.
        # Cleared between enrichment passes via the natural process
        # restart (the enricher subprocess is short-lived per pass).
        self._va_folder_cache: Dict[str, bool] = {}

    def can_enrich_artist(self) -> bool:
        """This plugin can enrich artist metadata from folder structure"""
        return True

    def can_enrich_album(self) -> bool:
        """This plugin can enrich album metadata from folder structure"""
        return True

    def can_enrich_track(self) -> bool:
        """This plugin can enrich track metadata from filename"""
        return True

    def _extract_track_number_and_title(
        self, filename: str
    ) -> tuple[Optional[int], str, Optional[str]]:
        """
        Extract track number, clean title, and artist from filename.

        Args:
            filename: The filename without extension

        Returns:
            Tuple of (track_number, clean_title, artist_name)
        """
        artist_name = None
        track_number, clean_title = self._strip_leading_number(filename)

        # Try to split the clean title into "artist - track" format
        # Handle various dash variants: -, –, —, and with/without spaces
        dash_patterns = [
            " - ",  # space-dash-space
            " – ",  # space-en-dash-space
            " — ",  # space-em-dash-space
            "-",  # just dash
            "–",  # en-dash
            "—",  # em-dash
        ]

        for dash_pattern in dash_patterns:
            if dash_pattern in clean_title:
                parts = clean_title.split(dash_pattern, 1)
                if len(parts) == 2:
                    potential_artist = parts[0].strip()
                    potential_title = parts[1].strip()

                    # Only accept if both parts are non-empty and reasonable length
                    if (
                        potential_artist
                        and potential_title
                        and len(potential_artist) > 0
                    ):
                        artist_name = potential_artist
                        clean_title = potential_title
                        break

        # A track number can sit after the "Artist - " prefix
        # ("Artist - 01.Title"); retry on the post-split title.
        if track_number is None:
            track_number, clean_title = self._strip_leading_number(clean_title)

        return track_number, clean_title, artist_name

    def _strip_leading_number(self, text: str) -> tuple[Optional[int], str]:
        """Split a leading "NN." track number off, if present."""
        for pattern in self.track_number_patterns:
            match = pattern.match(text)
            if match:
                try:
                    return int(match.group(1)), text[match.end() :].strip()
                except (ValueError, IndexError):
                    continue
        return None, text

    def _parse_album_folder(self, album_folder: str) -> tuple[str, Optional[str]]:
        """
        Parse album folder name to extract album title and possibly artist.

        Args:
            album_folder: The album folder name

        Returns:
            Tuple of (album_title, artist_name)
        """
        # Check if folder is in "Artist - Album" format
        if " - " in album_folder:
            parts = album_folder.split(" - ", 1)
            if len(parts) == 2:
                artist_name = parts[0].strip()
                album_title = parts[1].strip()
                return album_title, artist_name

        # Return folder name as album title with no artist
        return album_folder, None

    async def _find_or_create_artist(self, artist_name: str) -> str:
        """
        Find existing artist by case-insensitive name match or create new one.

        Args:
            artist_name: The artist name to find or create

        Returns:
            The artist ID (existing or newly created)
        """
        artist_name = clean_display_name(artist_name)
        if not artist_name:
            return "unknown_artist"

        # Search for existing artist with case-insensitive match
        search_results, _ = await self.db_manager.search_artists(
            artist_name, limit=100
        )

        for artist in search_results:
            if artist["name"].lower() == artist_name.lower():
                logger.debug(
                    f"Found existing artist: {artist['name']} (ID: {artist['id']})"
                )
                return artist["id"]

        # No existing artist found, create new one
        artist_id = generate_artist_id(artist_name)
        existing_artist = await self.db_manager.get_artist_by_id(artist_id)

        if not existing_artist:
            await self.db_manager.insert_artist(
                {
                    "id": artist_id,
                    "name": artist_name,
                    "enriched": 0,
                    "last_updated": int(time.time()),
                }
            )
            logger.debug(f"Created new artist: {artist_name} (ID: {artist_id})")
        else:
            logger.debug(f"Artist already exists with ID: {artist_id}")

        return artist_id

    async def _find_or_create_album(
        self, album_title: str, artist_id: str, file_path: str
    ) -> str:
        """
        Return the album ID for (title, folder), creating a row if absent.

        With the folder-bounded album ID, the lookup is deterministic:
        two calls with the same cleaned title and the same album folder
        always produce the same ID, so we go straight to a
        ``get_album_by_id`` check — no separate title/artist search.

        Args:
            album_title: The album title to find or create.
            artist_id: The artist ID stored on the new row (not part of
                the ID key — only used as the album's "anchor artist").
            file_path: A track's file path, used to derive the album
                folder so quality variants in sibling directories get
                distinct IDs.

        Returns:
            The album ID (existing or newly created).
        """
        album_title = clean_display_name(album_title)
        if not album_title:
            return "unknown_album"

        album_id = generate_album_id(album_title, album_folder_for_path(file_path))
        existing = await self.db_manager.get_album_by_id(album_id)
        if existing:
            logger.debug(f"Album already exists with ID: {album_id}")
            return album_id

        await self.db_manager.insert_album(
            {
                "id": album_id,
                "title": album_title,
                "artist_id": artist_id,
                "enriched": 0,
                "last_updated": int(time.time()),
            }
        )
        logger.debug(f"Created new album: {album_title} by {artist_id} (ID: {album_id})")
        return album_id

    async def _track_is_in_va_folder(self, file_path: str) -> bool:
        """Return True when this track's directory looks like a V/A
        compilation folder by the same criteria the indexer uses to
        detach tracks (``orphan_va_folder_tracks``):

          * ≥ ``VA_MIN_DISTINCT_ARTISTS`` distinct real artists
            (``unknown_artist`` excluded) own a track here, AND
          * the unique-artist-per-track ratio is ≥
            ``VA_MIN_ARTIST_UNIQUENESS``.

        Cached per folder on the plugin instance so a 69-track V/A
        folder only triggers two DB queries total, not 138.
        """
        if not file_path:
            return False
        folder = album_folder_for_path(file_path)
        if not folder:
            return False
        cached = self._va_folder_cache.get(folder)
        if cached is not None:
            return cached
        try:
            n_artists = await self.db_manager.count_distinct_artists_in_folder(folder)
            n_tracks = await self.db_manager.count_tracks_in_folder(folder)
        except Exception as e:
            logger.debug(f"Could not assess V/A status for {folder}: {e}")
            self._va_folder_cache[folder] = False
            return False
        is_va = (
            n_artists >= self.VA_MIN_DISTINCT_ARTISTS
            and n_tracks > 0
            and (n_artists / n_tracks) >= self.VA_MIN_ARTIST_UNIQUENESS
        )
        self._va_folder_cache[folder] = is_va
        return is_va

    def _find_containing_music_folder(self, file_path: str) -> Optional[str]:
        """
        Find which configured music folder contains the given file path.

        Args:
            file_path: Full path to the audio file

        Returns:
            The music folder path that contains the file, or None if not found
        """
        if not file_path:
            return None

        # Normalize the file path
        normalized_file_path = str(Path(file_path).resolve())

        # Check each music folder to see if it contains the file
        for music_folder in self.music_folders:
            try:
                # Check if the file path starts with the music folder path
                if (
                    normalized_file_path.startswith(music_folder + os.sep)
                    or normalized_file_path == music_folder
                ):
                    return music_folder
            except Exception as e:
                logger.debug(f"Error checking music folder {music_folder}: {e}")
                continue

        return None

    def _extract_metadata_from_path(self, file_path: str) -> Dict[str, Optional[str]]:
        """
        Extract metadata from file path structure within the configured music folders.

        Expected structure: MusicFolder/Artist/Album/track.ext or MusicFolder/Album/track.ext

        Args:
            file_path: Full path to the audio file

        Returns:
            Dictionary with extracted metadata
        """
        if not file_path:
            return {}

        # Find which music folder contains this file
        containing_music_folder = self._find_containing_music_folder(file_path)
        if not containing_music_folder:
            logger.debug(
                f"File path not within any configured music folder: {file_path}"
            )
            return {}

        # Get the relative path from the music folder
        try:
            relative_path = os.path.relpath(file_path, containing_music_folder)
        except Exception as e:
            logger.debug(
                f"Could not get relative path for {file_path} from {containing_music_folder}: {e}"
            )
            return {}

        # Split the relative path into parts
        path_parts = relative_path.split(os.sep)

        if len(path_parts) < 1:
            logger.debug(
                f"Relative path too short to extract metadata: {relative_path}"
            )
            return {}

        # Get filename without extension
        filename = os.path.splitext(path_parts[-1])[0]

        # Extract track number and clean title from filename
        track_number, title, artist_from_filename = (
            self._extract_track_number_and_title(filename)
        )

        # Handle album and artist based on path depth within music folder
        album_title = None
        artist_name = artist_from_filename  # Prioritize artist from filename

        if len(path_parts) >= 2:
            # At least one folder level: treat second-to-last as album folder
            album_folder = path_parts[-2]
            album_title, artist_from_folder = self._parse_album_folder(album_folder)

            # If no artist from filename, use artist from album folder
            if not artist_name:
                artist_name = artist_from_folder

            # If still no artist and we have another folder level, use it as artist
            if not artist_name and len(path_parts) >= 3:
                artist_name = path_parts[-3]

        metadata = {
            "title": title if title else filename,
            "track_number": track_number,
        }

        if album_title:
            metadata["album"] = album_title

        if artist_name:
            metadata["artist"] = artist_name

        logger.debug(
            f"Extracted metadata from {file_path} (relative: {relative_path}): {metadata}"
        )
        return metadata

    async def enrich_artist(self, artist: Dict) -> Optional[Dict]:
        """
        Enrich artist metadata from filesystem.
        """
        return None

    async def enrich_album(self, album: Dict) -> Optional[Dict]:
        """
        Enrich album metadata from filesystem.
        """
        return None

    async def enrich_track(self, track: Dict) -> Optional[Dict]:
        """
        Enrich track metadata using filesystem structure.

        This method handles creation/update of artists and albums as needed,
        using case-insensitive matching to find existing entities.

        Args:
            track: Track dictionary containing at least 'file_path'

        Returns:
            Dictionary with updates or None if no enrichment possible
        """
        if not track.get("file_path"):
            logger.debug("No file_path in track data, cannot enrich from filesystem")
            return None

        # Extract metadata from file path
        fs_metadata = self._extract_metadata_from_path(track["file_path"])

        if not fs_metadata:
            logger.debug(
                f"Could not extract metadata from file path: {track.get('file_path')}"
            )
            return None

        updates = {}
        changed_items = {"artists": set(), "albums": set()}

        # Handle track title. A title equal to the basename OR its stem is
        # still just a filename echo (a prior pass may have stripped only the
        # extension), so a re-run can keep improving it — e.g. dropping a
        # "1."-prefix once the number patterns learn to match it.
        current_title = track.get("title", "")
        basename = os.path.basename(track["file_path"])
        stem = os.path.splitext(basename)[0]
        if not current_title or current_title in (basename, stem):
            if fs_metadata.get("title") and fs_metadata["title"] != current_title:
                updates["title"] = fs_metadata["title"]

        # Handle track number
        if not track.get("track_number") and fs_metadata.get("track_number"):
            updates["track_number"] = fs_metadata["track_number"]

        # Handle artist - only update if current artist is unknown
        current_artist_id = track.get("artist_id", "unknown_artist")
        fs_artist = fs_metadata.get("artist")
        if current_artist_id == "unknown_artist" and fs_artist:
            new_artist_id = await self._find_or_create_artist(fs_artist)
            if new_artist_id != current_artist_id:
                updates["artist_id"] = new_artist_id
                changed_items["artists"].add(new_artist_id)
                logger.debug(
                    f"Updated track artist from {current_artist_id} to {new_artist_id}"
                )

        # Handle album - only update if current album is unknown,
        # AND only when the track is NOT in a V/A folder. The indexer's
        # detach pass deliberately moves V/A-folder tracks to
        # ``unknown_album``; re-anchoring them here would undo it.
        current_album_id = track.get("album_id", "unknown_album")
        fs_album = fs_metadata.get("album")
        if current_album_id == "unknown_album" and fs_album:
            if await self._track_is_in_va_folder(track.get("file_path", "")):
                logger.debug(
                    f"Track {track.get('id')} is in a V/A folder; "
                    f"leaving in unknown_album so it surfaces under its real "
                    f"artist via the orphan-tracks fallback"
                )
            else:
                # Use the artist_id from the track (either existing or newly updated)
                artist_id_for_album = updates.get("artist_id", current_artist_id)

                # If we still don't have a valid artist, try to create one from filesystem
                if artist_id_for_album == "unknown_artist" and fs_artist:
                    artist_id_for_album = await self._find_or_create_artist(fs_artist)
                    updates["artist_id"] = artist_id_for_album
                    changed_items["artists"].add(artist_id_for_album)

                new_album_id = await self._find_or_create_album(
                    fs_album, artist_id_for_album, track["file_path"]
                )
                if new_album_id != current_album_id:
                    updates["album_id"] = new_album_id
                    changed_items["albums"].add(new_album_id)
                    logger.debug(
                        f"Updated track album from {current_album_id} to {new_album_id}"
                    )

        if updates:
            logger.info(
                f"Filesystem fallback enriched track {track.get('title', 'Unknown')}: {list(updates.keys())}"
            )

            result = {"updates": updates}

            # Include changed items if we created/updated artists or albums
            if any(changed_items.values()):
                result["changed_items"] = {
                    "artists": list(changed_items["artists"]),
                    "albums": list(changed_items["albums"]),
                    "tracks": [],  # We don't create new tracks in this plugin
                }
                logger.debug(f"Created/updated entities: {result['changed_items']}")

            # Important: We don't set 'enriched' flag to allow other plugins to re-check
            return result

        return None
