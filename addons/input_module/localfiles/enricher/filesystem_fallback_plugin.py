import logging
import os
import re
from typing import Dict, Optional

from ..config_model import LocalFilesConfig

try:
    from enricher_plugin import EnricherPlugin
except ImportError:
    from .enricher_plugin import EnricherPlugin

try:
    from id_generator import generate_artist_id, generate_album_id
except ImportError:
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

    Enrichment behavior:
    - Artist enrichment: Updates artist name only if current name is "unknown"
    - Album enrichment: Updates album title only if current title is "unknown"
    - Track enrichment: Updates track metadata and creates/finds artists and albums as needed
    - Uses case-insensitive matching to find existing artists and albums
    - Creates new entities if not found, otherwise reuses existing ones

    This plugin does NOT set the "enriched" flag to allow other plugins to re-check the data.
    """

    def __init__(self, config: LocalFilesConfig, db_manager):
        self.config = config
        self.db_manager = db_manager

        # Track number regex patterns
        self.track_number_patterns = [
            r"^(\d+)\.?\s+",  # "1. " or "1 " or "1."
            r"^(\d+)-\s+",  # "1- "
            r"^(\d+)_\s+",  # "1_ "
        ]

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
        track_number = None
        clean_title = filename
        artist_name = None

        # First, try to extract track number if present
        for pattern in self.track_number_patterns:
            match = re.match(pattern, filename)
            if match:
                try:
                    track_number = int(match.group(1))
                    clean_title = filename[match.end() :].strip()
                    break
                except (ValueError, IndexError):
                    continue

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

        return track_number, clean_title, artist_name

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
        if not artist_name or artist_name.strip() == "":
            return "unknown_artist"

        # Search for existing artist with case-insensitive match
        search_results, _ = await self.db_manager.search_artists(
            artist_name.strip(), limit=100
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
                    "name": artist_name.strip(),
                    "enriched": 0,
                    "last_updated": int(__import__("time").time()),
                }
            )
            logger.debug(f"Created new artist: {artist_name} (ID: {artist_id})")
        else:
            logger.debug(f"Artist already exists with ID: {artist_id}")

        return artist_id

    async def _find_or_create_album(self, album_title: str, artist_id: str) -> str:
        """
        Find existing album by case-insensitive title and artist match or create new one.

        Args:
            album_title: The album title to find or create
            artist_id: The artist ID for the album

        Returns:
            The album ID (existing or newly created)
        """
        if not album_title or album_title.strip() == "":
            return "unknown_album"

        album_title = album_title.strip()

        # First try exact match by title and artist
        existing_album = await self.db_manager.get_album_by_title_and_artist(
            album_title, artist_id
        )

        if existing_album:
            logger.debug(
                f"Found existing album: {existing_album['title']} (ID: {existing_album['id']})"
            )
            return existing_album["id"]

        # If no exact match, we need to do a case-insensitive search
        # This is a simple implementation - for a full solution, you'd want a search_albums method in the DB
        # For now, let's check if an album with the generated ID already exists
        album_id = generate_album_id(album_title, artist_id)
        existing_album_by_id = await self.db_manager.get_album_by_id(album_id)

        if existing_album_by_id:
            # Album exists but title might have different case - check case-insensitively
            if existing_album_by_id["title"].lower() == album_title.lower():
                logger.debug(
                    f"Found existing album with different case: {existing_album_by_id['title']} (ID: {album_id})"
                )
                return album_id

        # Create new album
        if not existing_album_by_id:
            await self.db_manager.insert_album(
                {
                    "id": album_id,
                    "title": album_title,
                    "artist_id": artist_id,
                    "enriched": 0,
                    "last_updated": int(__import__("time").time()),
                }
            )
            logger.debug(
                f"Created new album: {album_title} by {artist_id} (ID: {album_id})"
            )
        else:
            logger.debug(f"Album already exists with ID: {album_id}")

        return album_id

    def _extract_metadata_from_path(self, file_path: str) -> Dict[str, Optional[str]]:
        """
        Extract metadata from file path structure.

        Expected structure: .../Artist/Album/track.ext or .../Album/track.ext

        Args:
            file_path: Full path to the audio file

        Returns:
            Dictionary with extracted metadata
        """
        if not file_path:
            return {}

        # Normalize path and split into parts
        path_parts = os.path.normpath(file_path).split(os.sep)

        if len(path_parts) < 2:
            logger.debug(f"File path too short to extract metadata: {file_path}")
            return {}

        # Get filename without extension
        filename = os.path.splitext(path_parts[-1])[0]

        # Extract track number and clean title from filename
        track_number, title, artist_from_filename = (
            self._extract_track_number_and_title(filename)
        )

        # Get album folder (second to last part of path)
        album_folder = path_parts[-2]
        album_title, artist_from_folder = self._parse_album_folder(album_folder)

        # Prioritize artist from filename over folder structure
        artist_name = artist_from_filename
        if not artist_name:
            # Fall back to artist from album folder
            artist_name = artist_from_folder
            if not artist_name and len(path_parts) >= 3:
                # Use the third-to-last path component as artist
                artist_name = path_parts[-3]

        metadata = {
            "title": title if title else filename,
            "album": album_title,
            "track_number": track_number,
        }

        if artist_name:
            metadata["artist"] = artist_name

        logger.debug(f"Extracted metadata from {file_path}: {metadata}")
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

        # Handle track title
        current_title = track.get("title", "")
        if not current_title or current_title == os.path.basename(track["file_path"]):
            if fs_metadata.get("title"):
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

        # Handle album - only update if current album is unknown
        current_album_id = track.get("album_id", "unknown_album")
        fs_album = fs_metadata.get("album")
        if current_album_id == "unknown_album" and fs_album:
            # Use the artist_id from the track (either existing or newly updated)
            artist_id_for_album = updates.get("artist_id", current_artist_id)

            # If we still don't have a valid artist, try to create one from filesystem
            if artist_id_for_album == "unknown_artist" and fs_artist:
                artist_id_for_album = await self._find_or_create_artist(fs_artist)
                updates["artist_id"] = artist_id_for_album
                changed_items["artists"].add(artist_id_for_album)

            new_album_id = await self._find_or_create_album(
                fs_album, artist_id_for_album
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
