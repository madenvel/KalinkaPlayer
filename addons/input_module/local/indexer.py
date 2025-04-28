import os
import threading
import logging
from enum import Enum
from typing import List

from .filedb import FileDb
from data_model.datamodel import Track, Album, Artist
import datetime

logger = logging.getLogger(__name__)


class IndexerStatus(Enum):
    """Enum representing the status of the indexer process."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETE = "complete"


class FileIndexer:
    """A class that indexes audio files in a directory and adds them to the database."""

    def __init__(self, base_path: str, db: FileDb):
        """Initialize the indexer.

        Args:
            base_path: Base path to scan for audio files
            db: FileDb instance to add tracks to
        """
        self.base_path = os.path.abspath(base_path)
        self.db = db
        self.status = IndexerStatus.NOT_STARTED
        self.index_thread = None
        self.audio_extensions = {".mp3", ".flac"}
        self.indexed_files: List[str] = []  # List to store indexed file paths
        self.cleanup_missing_files = True  # Flag to control cleaning up missing files

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

    def _index_files(self) -> None:
        """Index all audio files in the base path and add them to the database."""
        try:
            logger.info(f"Starting indexing of {self.base_path}")

            # Clear the previous list of indexed files
            self.indexed_files = []
            found_file_ids = []

            for root, _, files in os.walk(self.base_path):
                for file in files:
                    _, ext = os.path.splitext(file)
                    if ext.lower() in self.audio_extensions:
                        try:
                            # Get the absolute path of the file
                            file_path = os.path.join(root, file)

                            # Create the relative path to use as ID
                            rel_path = os.path.relpath(file_path, self.base_path)

                            # Add to the list of indexed files
                            self.indexed_files.append(rel_path)
                            found_file_ids.append(rel_path)

                            # Get the filename without extension as title
                            title = os.path.splitext(os.path.basename(file))[0]

                            # Create a track object
                            track = Track(
                                id=rel_path,
                                title=title,
                            )

                            # Add the track to the database
                            # Get the file modification timestamp in ISO format
                            file_update_ts = os.path.getmtime(file_path)
                            # Convert timestamp to ISO 8601 format
                            iso_timestamp = datetime.datetime.fromtimestamp(
                                file_update_ts
                            ).isoformat()
                            track.file_update_ts = iso_timestamp

                            self.db.add_track(track)
                            logger.debug(f"Added track: {rel_path}")
                        except Exception as e:
                            logger.error(f"Error processing file {file}: {e}")

            # Clean up missing files if enabled
            if self.cleanup_missing_files and found_file_ids:
                self._remove_missing_files(found_file_ids)

            logger.info("Indexing completed successfully")
        except Exception as e:
            logger.error(f"Indexing failed: {e}")
        finally:
            self.status = IndexerStatus.COMPLETE

    def _remove_missing_files(self, found_file_ids: List[str]) -> None:
        """Remove files from the database that no longer exist in the filesystem.

        Args:
            found_file_ids: List of file IDs (relative paths) found during indexing
        """
        try:
            # Find tracks in DB that are not in the list of found files
            missing_track_ids = self.db.get_tracks_not_in_list(found_file_ids)

            if not missing_track_ids:
                logger.info("No missing files to clean up")
                return

            # Delete each missing track from the database
            deleted_count = 0
            for track_id in missing_track_ids:
                if self.db.delete_track(track_id):
                    deleted_count += 1
                    logger.debug(f"Removed missing track: {track_id}")

            logger.info(
                f"Cleanup complete. Removed {deleted_count} tracks no longer in filesystem"
            )
        except Exception as e:
            logger.error(f"Error cleaning up missing files: {e}")
