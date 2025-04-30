import hashlib
import os
import threading
import logging
from enum import Enum
from typing import List

from .filedb import EnrichmentState, FileDb
from data_model.datamodel import AlbumImage, Genre, Track, Album, Artist
from .tagextractor import TagExtractor
import datetime

logger = logging.getLogger(__name__)


class IndexerStatus(Enum):
    """Enum representing the status of the indexer process."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETE = "complete"


def name_to_id(name: str) -> str:
    """Convert a name to a unique ID.

    Args:
        name: Name to convert
    Returns:
        Unique ID string
    """
    return hashlib.md5(name.encode()).hexdigest()


class FileIndexer:
    """A class that indexes audio files in a directory and adds them to the database."""

    def __init__(self, base_path: str, db: FileDb, resource_path: str = ""):
        """Initialize the indexer.

        Args:
            base_path: Base path to scan for audio files
            db: FileDb instance to add tracks to
            resource_path: Path to resources, i.g. album art, relative to base_path
        """
        self.base_path = os.path.abspath(base_path)
        self.resource_path = os.path.join(self.base_path, resource_path)
        self.db = db
        self.status = IndexerStatus.NOT_STARTED
        self.index_thread = None
        self.audio_extensions = {".mp3", ".flac"}
        self.indexed_files: List[str] = []  # List to store indexed file paths
        self.cleanup_missing_files = True  # Flag to control cleaning up missing files
        self.discovered_albums = dict()  # Set to store discovered albums
        self.discovered_artists = dict()  # Set to store discovered artists

    def start_indexing(self, cleanup_missing_files: bool = True) -> None:
        """Start the indexing process in a new thread.

        Args:
            cleanup_missing_files: If True, files no longer in the filesystem will be removed from DB
        """
        if self.status == IndexerStatus.RUNNING:
            logger.warning("Indexing is already running")
            return

        self.cleanup_missing_files = cleanup_missing_files
        self.status = IndexerStatus.RUNNING
        self.index_thread = threading.Thread(target=self._index_files)
        self.index_thread.daemon = True
        self.index_thread.start()

    def get_status(self) -> str:
        """Get the current status of the indexer.

        Returns:
            Status string: 'running', 'not_started', or 'complete'
        """
        return self.status.value

    def get_indexed_files(self) -> List[str]:
        """Get the list of indexed file paths (relative to base path).

        Returns:
            List of indexed relative file paths
        """
        return self.indexed_files.copy()

    def _run(self) -> None:
        """Run the indexing process.

        This method is called in a separate thread.
        """
        try:
            self._index_files()
            self._enrich_files()
        except KeyboardInterrupt:
            logger.info("Indexing interrupted by user")
            self.status = IndexerStatus.COMPLETE
        except Exception as e:
            logger.error(f"Error during indexing: {e}")
            self.status = IndexerStatus.COMPLETE
        finally:
            self.status = IndexerStatus.COMPLETE

    def _index_files(self) -> None:
        """Index all audio files in the base path and add them to the database."""
        try:
            logger.info(f"Starting indexing of {self.base_path}")

            # Clear the previous list of indexed files
            self.indexed_files = []
            found_file_ids = []

            for file_path in self._get_audiofiles_in_directory():
                try:

                    [track, file_update_ts, enrichment_state] = (
                        self._process_single_file(file_path)
                    )

                    tracks_updated = self.db.add_or_update_track(
                        track,
                        file_update_ts=file_update_ts,
                        enrichment_state=enrichment_state,
                    )

                    if tracks_updated > 0:
                        logger.debug(f"Added or updated track: {file_path}")

                    found_file_ids.append(track.id)

                except Exception as e:
                    logger.error(f"Error processing file {file_path}: {e}")

            # Clean up missing files if enabled
            if self.cleanup_missing_files and found_file_ids:
                self._remove_missing_files(found_file_ids)

            if self.discovered_albums:
                for album in self.discovered_albums.values():
                    if self.db.add_or_enrich_album(album):
                        logger.info(f"Added album: {album.title}")

            if self.discovered_artists:
                for artist in self.discovered_artists.values():
                    if self.db.add_or_enrich_artist(artist):
                        logger.info(f"Added artist: {artist.name}")

            logger.info("Indexing completed successfully")
        except Exception as e:
            logger.error(f"Indexing failed: {e}")
        finally:
            self.status = IndexerStatus.COMPLETE

    def _get_audiofiles_in_directory(self):
        """Get a list of audio files in the base directory.

        Returns:
            List of file paths
        """
        for root, _, files in os.walk(self.base_path):
            for file in files:
                _, ext = os.path.splitext(file)
                if ext.lower() in self.audio_extensions:
                    yield os.path.join(root, file)

    def _process_single_file(self, file_path: str) -> List:
        """Process a single audio file and extract metadata.

        Args:
            file_path: Path to the audio file
        Returns:
            List of Track objects
        """
        # Create the relative path to use as ID
        rel_path = os.path.relpath(file_path, self.base_path)

        # Add to the list of indexed files
        self.indexed_files.append(rel_path)

        # Get the filename without extension as title
        title = os.path.splitext(os.path.basename(file_path))[0]

        # Create a track object
        track = Track(id=name_to_id(file_path), title=title, duration=0)

        enriched = self._enrich_with_tags_data(track, file_path)

        # Add the track to the database
        # Get the file modification timestamp in ISO format
        file_update_ts = os.path.getmtime(file_path)
        # Convert timestamp to ISO 8601 format
        iso_timestamp = datetime.datetime.fromtimestamp(file_update_ts).isoformat()

        return [track, iso_timestamp, EnrichmentState.SOME if enriched else None]

    def _enrich_with_tags_data(self, track: Track, file_path: str) -> bool:
        """Enrich the track object with metadata from the file.

        Args:
            track: Track object to enrich
            file_path: Path to the audio file
        """
        try:
            tag_extractor = TagExtractor(file_path)

            # Extract metadata from tags
            title = tag_extractor.get_title()
            track.title = title if title else track.title
            # genre = tag_extractor.get_genre()
            # year = tag_extractor.get_year()
            duration = tag_extractor.get_duration()
            track.duration = duration if duration else 0
            album_name = tag_extractor.get_album()
            artist_name = tag_extractor.get_artist()

            # Create Album and Artist objects if they exist
            if album_name:
                album_id = name_to_id(album_name)
                album = Album(id=album_id, title=album_name)
                self._save_album_art(tag_extractor, album)
                track.album = album
                self.discovered_albums[album_id] = album

            if artist_name:
                artist_id = name_to_id(artist_name)
                artist = Artist(id=artist_id, name=artist_name)
                track.performer = artist
                self.discovered_artists[artist_id] = artist

            return album_name and artist_name and title

        except Exception as e:
            logger.error(f"Error extracting tags from {file_path}: {e}")

    def _save_album_art(self, tag_extractor: TagExtractor, album: Album) -> None:
        """Save album art to the database.

        Args:
            tag_extractor: TagExtractor instance to extract album art
            album: Album object to save
        """
        try:
            saved_path = tag_extractor.save_album_art(
                os.path.join(self.resource_path, album.id)
            )

            if saved_path is not None:
                logger.info(f"Saved album art for {album.title}")
                album.image = AlbumImage(
                    small=saved_path, large=saved_path, thumbnail=saved_path
                )
        except Exception as e:
            logger.error(f"Error saving album art for {album.title}: {e}")

    def _enrich_files(self):
        """Enrich files with additional metadata (e.g., album, artist) if needed."""
        pass

    def _remove_missing_files(self, found_file_ids: List[str]) -> None:
        """Remove files from the database that no longer exist in the filesystem.

        Args:
            found_file_ids: List of file IDs (relative paths) found during indexing
        """
        try:
            # Get all tracks not in the list of found files
            missing_tracks = self.db.get_tracks_not_in_list(found_file_ids)

            if not missing_tracks:
                logger.info("No missing files to clean up")
                return

            # Process in batches to avoid long-running transactions
            batch_size = 100
            processed_count = 0
            deleted_count = 0

            # Process tracks in batches
            for i in range(0, len(missing_tracks), batch_size):
                batch = missing_tracks[i : i + batch_size]
                processed_count += len(batch)

                # Process each track in the batch
                for track_id in batch:
                    if self.db.delete_track(track_id):
                        deleted_count += 1
                        logger.debug(f"Removed missing track: {track_id}")

                # Log progress for large cleanups
                if processed_count % 500 == 0:
                    logger.info(
                        f"Cleanup in progress: processed {processed_count} tracks, deleted {deleted_count}"
                    )

            logger.info(
                f"Cleanup complete. Removed {deleted_count} tracks no longer in filesystem"
            )
        except Exception as e:
            logger.error(f"Error cleaning up missing files: {e}")
