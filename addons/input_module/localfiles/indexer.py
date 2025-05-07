import os
import logging
import time
import hashlib
import threading
from typing import Dict, Optional, Set
import uuid
import mutagen
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from PIL import Image
import io
import threading
import queue
import mimetypes

# Import ID generation utilities
from .utils.id_generator import generate_artist_id, generate_album_id, generate_track_id

logger = logging.getLogger(__name__.split(".")[-1])

# Global variables to manage indexer state
_indexer_thread = None
_indexer_instance = None
_indexer_queue = queue.Queue()
_enricher_trigger_callback = None


class FileIndexer:
    def __init__(self, config, db_manager):
        self.config = config
        self.db_manager = db_manager
        self.music_folders = [
            folder.strip() for folder in config["music_folders"].split(",")
        ]
        self.artwork_path = config["artwork_path"]
        self.running = False
        self.lock = threading.Lock()

    def start(self):
        """Start the indexer process"""
        if self.running:
            logger.warning("Indexer already running, skipping")
            return

        with self.lock:
            self.running = True
            try:
                self.run_scan()
                logger.info("Indexer scan completed")
            except Exception as e:
                logger.error(f"Error running indexer scan: {str(e)}")
            finally:
                self.running = False

    def run_scan(self):
        """Scan all music folders for files"""
        logger.info(f"Starting music file scan in folders: {self.music_folders}")

        changed_items = {
            "artists": set(),
            "albums": set(),
            "tracks": set(),
        }

        for folder in self.music_folders:
            if not os.path.exists(folder):
                logger.warning(f"Music folder does not exist: {folder}")
                continue

            logger.info(f"Scanning folder: {folder}")
            self.scan_folder(folder, changed_items)

        # Delete stale entries after scanning but before enrichment
        cleanup_results = self.cleanup_stale_tracks()

        # If we removed any tracks, ensure we don't trigger enrichment for them
        if cleanup_results["tracks"] > 0:
            logger.info(
                "Removed stale tracks from database, proceeding with enrichment for valid tracks only"
            )

        # If anything changed and we have an enricher callback, notify it
        if any(changed_items.values()) and _enricher_trigger_callback:
            logger.info(
                f"Scan completed with changes: Artists={len(changed_items['artists'])}, "
                f"Albums={len(changed_items['albums'])}, Tracks={len(changed_items['tracks'])}"
            )
            _enricher_trigger_callback({"changed_items": changed_items})
        else:
            logger.info("Scan completed with no changes")
            _enricher_trigger_callback("scan_complete")

    def scan_folder(self, folder: str, changed_items: Dict[str, Set[str]]):
        """Recursively scan a folder for music files"""
        for root, _, files in os.walk(folder):
            for file in files:
                if self._is_supported_audio_file(file):
                    file_path = os.path.join(root, file)
                    try:
                        changes = self.process_file(file_path)
                        if changes:
                            for key, value in changes.items():
                                changed_items[key].add(value)
                    except Exception as e:
                        logger.error(f"Error processing file {file_path}: {str(e)}")

    def _is_supported_audio_file(self, filename: str) -> bool:
        """Check if the file is a supported audio format"""
        ext = os.path.splitext(filename.lower())[1]
        return ext in [".mp3", ".flac"]

    def process_file(self, file_path: str):
        """Process a music file and update the database. Returns changed items IDs."""
        # Check if file exists in database and if it has been modified
        stat = os.stat(file_path)
        file_size = stat.st_size
        modified_time = int(stat.st_mtime)

        # Check if the file is already in the database
        existing_track = self.db_manager.get_track_by_path(file_path)
        if (
            existing_track
            and existing_track["modified_time"] == modified_time
            and existing_track["file_size"] == file_size
        ):
            logger.debug(f"File unchanged, skipping: {file_path}")
            return None

        # Extract metadata from the file
        metadata = self._extract_metadata(file_path)
        if not metadata:
            logger.warning(f"Failed to extract metadata from {file_path}")
            return None

        changes = {
            "artists": None,
            "albums": None,
            "tracks": None,
        }

        # Add file metadata
        metadata["file_path"] = file_path
        metadata["file_size"] = file_size
        metadata["modified_time"] = modified_time
        metadata["last_updated"] = int(time.time())

        # Process artist
        artist_name = metadata.get("artist", "Unknown Artist")
        artist_id = generate_artist_id(artist_name)

        # Check if artist exists, create if not
        artist = self.db_manager.get_artist_by_id(artist_id)
        if not artist:
            self.db_manager.insert_artist(
                {
                    "id": artist_id,
                    "name": artist_name,
                    "enriched": 0,
                    "last_updated": int(time.time()),
                }
            )
            changes["artists"] = artist_id

        # Process album
        album_title = metadata.get("album", "Unknown Album")
        album_id = generate_album_id(album_title, artist_id)

        # Check if album exists, create if not
        album = self.db_manager.get_album_by_id(album_id)
        if not album:
            album_data = {
                "id": album_id,
                "title": album_title,
                "artist_id": artist_id,
                "enriched": 0,
                "last_updated": int(time.time()),
            }

            # Process year if available
            if "year" in metadata:
                album_data["year"] = metadata["year"]

            # Process genre if available
            if "genre" in metadata:
                album_data["genre"] = metadata["genre"]

            # Process album art if available
            if "album_art" in metadata:
                cover_art_filename = f"{album_id}.jpg"
                self._save_images(metadata["album_art"], album_id, "album")
                album_data["cover_art"] = cover_art_filename

            self.db_manager.insert_album(album_data)
            changes["albums"] = album_id

        # Process track
        track_id = generate_track_id(file_path)

        track_data = {
            "id": track_id,
            "title": metadata.get("title", os.path.basename(file_path)),
            "album_id": album_id,
            "artist_id": artist_id,
            "duration": metadata.get("duration", 0),
            "track_number": metadata.get("track_number"),
            "file_path": file_path,
            "format": metadata.get("format", "unknown"),
            "file_size": file_size,
            "modified_time": modified_time,
            "replaygain_peak": metadata.get("replaygain_peak"),
            "replaygain_gain": metadata.get("replaygain_gain"),
            "enriched": 0,
            "last_updated": int(time.time()),
        }

        self.db_manager.insert_track(track_data)
        changes["tracks"] = track_id

        # Update album statistics
        self.db_manager.update_album_stats(album_id)

        logger.debug(f"Processed file: {file_path}")
        return changes

    def _extract_metadata(self, file_path: str) -> Optional[Dict]:
        """Extract metadata from a music file"""
        try:
            # Determine file format
            ext = os.path.splitext(file_path.lower())[1]

            if ext == ".mp3":
                return self._extract_mp3_metadata(file_path)
            elif ext == ".flac":
                return self._extract_flac_metadata(file_path)
            else:
                logger.warning(f"Unsupported file format: {file_path}")
                return None

        except Exception as e:
            logger.error(f"Error extracting metadata from {file_path}: {str(e)}")
            return None

    def _extract_mp3_metadata(self, file_path: str) -> Dict:
        """Extract metadata from an MP3 file"""
        try:
            mp3 = MP3(file_path)
            id3 = ID3(file_path)

            metadata = {
                "format": mimetypes.guess_type(file_path)[0] or "audio/mpeg",
                "duration": int(mp3.info.length),  # Store in seconds
            }

            # Extract basic tags
            if "TIT2" in id3:  # Title
                metadata["title"] = str(id3["TIT2"])

            if "TPE1" in id3:  # Artist
                metadata["artist"] = str(id3["TPE1"])

            if "TALB" in id3:  # Album
                metadata["album"] = str(id3["TALB"])

            if "TRCK" in id3:  # Track number
                track_str = str(id3["TRCK"])
                if "/" in track_str:
                    track_str = track_str.split("/")[0]
                try:
                    metadata["track_number"] = int(track_str)
                except ValueError:
                    pass

            if "TDRC" in id3:  # Year
                try:
                    metadata["year"] = int(str(id3["TDRC"]).split("-")[0])
                except (ValueError, IndexError):
                    pass

            if "TCON" in id3:  # Genre
                metadata["genre"] = str(id3["TCON"])

            # Extract ReplayGain info
            if "TXXX:replaygain_track_gain" in id3:
                gain_str = str(id3["TXXX:replaygain_track_gain"])
                try:
                    # Extract the numeric part and convert to float
                    metadata["replaygain_gain"] = float(gain_str.replace(" dB", ""))
                except ValueError:
                    pass

            if "TXXX:replaygain_track_peak" in id3:
                peak_str = str(id3["TXXX:replaygain_track_peak"])
                try:
                    metadata["replaygain_peak"] = float(peak_str)
                except ValueError:
                    pass

            # Extract album art
            for tag in ["APIC:", "APIC:Cover", "APIC:CoverFront"]:
                if tag in id3:
                    apic = id3[tag]
                    metadata["album_art"] = apic.data
                    break

            return metadata

        except Exception as e:
            logger.error(f"Error extracting MP3 metadata from {file_path}: {str(e)}")
            raise

    def _extract_flac_metadata(self, file_path: str) -> Dict:
        """Extract metadata from a FLAC file"""
        try:
            flac = FLAC(file_path)

            metadata = {
                "format": mimetypes.guess_type(file_path)[0] or "audio/flac",
                "duration": int(flac.info.length),  # Store in seconds
            }

            # Extract basic tags
            if "title" in flac:
                metadata["title"] = flac["title"][0]

            if "artist" in flac:
                metadata["artist"] = flac["artist"][0]

            if "album" in flac:
                metadata["album"] = flac["album"][0]

            if "tracknumber" in flac:
                track_str = flac["tracknumber"][0]
                if "/" in track_str:
                    track_str = track_str.split("/")[0]
                try:
                    metadata["track_number"] = int(track_str)
                except ValueError:
                    pass

            if "date" in flac:
                try:
                    metadata["year"] = int(flac["date"][0].split("-")[0])
                except (ValueError, IndexError):
                    pass

            if "genre" in flac:
                metadata["genre"] = flac["genre"][0]

            # Extract ReplayGain info
            if "replaygain_track_gain" in flac:
                gain_str = flac["replaygain_track_gain"][0]
                try:
                    metadata["replaygain_gain"] = float(gain_str.replace(" dB", ""))
                except ValueError:
                    pass

            if "replaygain_track_peak" in flac:
                peak_str = flac["replaygain_track_peak"][0]
                try:
                    metadata["replaygain_peak"] = float(peak_str)
                except ValueError:
                    pass

            # Extract album art
            pictures = flac.pictures
            if pictures:
                for pic in pictures:
                    if pic.type == 3:  # Cover (front)
                        metadata["album_art"] = pic.data
                        break
                else:  # If no cover front was found, use the first picture
                    metadata["album_art"] = pictures[0].data

            return metadata

        except Exception as e:
            logger.error(f"Error extracting FLAC metadata from {file_path}: {str(e)}")
            raise

    def _save_images(self, image_data: bytes, entity_id: str, entity_type: str):
        """Save artwork images in different sizes"""
        try:
            img = Image.open(io.BytesIO(image_data))

            # Create the directory if it doesn't exist
            dir_path = os.path.join(self.artwork_path, entity_type)
            os.makedirs(dir_path, exist_ok=True)

            # Convert to RGB if needed (for PNG, etc.)
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Save thumbnail (50x50)
            thumbnail = img.copy()
            thumbnail.thumbnail((50, 50), Image.LANCZOS)
            thumbnail.save(
                os.path.join(dir_path, f"{entity_id}_thumbnail.jpg"), "JPEG", quality=90
            )

            # Save small (230x230)
            small = img.copy()
            small.thumbnail((230, 230), Image.LANCZOS)
            small.save(
                os.path.join(dir_path, f"{entity_id}_small.jpg"), "JPEG", quality=90
            )

            # Save large (600x600 or original if smaller)
            large = img.copy()
            large.thumbnail((600, 600), Image.LANCZOS)
            large.save(
                os.path.join(dir_path, f"{entity_id}_large.jpg"), "JPEG", quality=90
            )

            return True
        except Exception as e:
            logger.error(
                f"Error saving artwork for {entity_type} {entity_id}: {str(e)}"
            )
            return False

    def _get_artist_id(self, artist_name: str) -> str:
        """Generate a stable ID for an artist"""
        if not artist_name or artist_name == "Unknown Artist":
            return "unknown_artist"

        # Create a hash from the artist name for a stable ID
        hash_obj = hashlib.md5(artist_name.lower().encode("utf-8"))
        return f"artist_{hash_obj.hexdigest()[:16]}"

    def _get_album_id(self, album_title: str, artist_id: str) -> str:
        """Generate a stable ID for an album"""
        if not album_title or album_title == "Unknown Album":
            return "unknown_album"

        # Create a hash from the album title and artist ID for a stable ID
        hash_input = f"{album_title.lower()}{artist_id}"
        hash_obj = hashlib.md5(hash_input.encode("utf-8"))
        return f"album_{hash_obj.hexdigest()[:16]}"

    def _get_track_id(self, file_path: str) -> str:
        """Generate a stable ID for a track"""
        # Create a hash from the file path for a stable ID
        hash_obj = hashlib.md5(file_path.encode("utf-8"))
        return f"track_{hash_obj.hexdigest()[:16]}"

    def cleanup_stale_tracks(self) -> Dict[str, int]:
        """Remove entries for files that no longer exist in the file system"""
        logger.info("Checking for stale files in the database...")

        # Get all tracks from the database
        all_tracks = self.db_manager.get_all_tracks()

        removed_tracks = 0

        # Check each track to see if the file still exists
        for track in all_tracks:
            file_path = track["file_path"]
            if not os.path.exists(file_path):
                logger.info(
                    f"File no longer exists, removing from database: {file_path}"
                )
                track_id = track["id"]
                self.db_manager.delete_track(track_id)
                removed_tracks += 1

        # Clean up orphaned albums and artists
        removed_albums, removed_artists = (
            self.db_manager.delete_orphaned_albums_and_artists()
        )

        if removed_tracks > 0 or removed_albums > 0 or removed_artists > 0:
            logger.info(
                f"Cleanup completed: {removed_tracks} tracks, {removed_albums} albums, "
                f"and {removed_artists} artists removed"
            )
        else:
            logger.info("No stale entries found in the database")

        return {
            "tracks": removed_tracks,
            "albums": removed_albums,
            "artists": removed_artists,
        }


def _indexer_worker(config, db_manager):
    """Background worker thread for the indexer"""
    global _indexer_instance

    _indexer_instance = FileIndexer(config, db_manager)

    # Run initial scan
    _indexer_instance.start()

    # Set up regular interval scanning
    interval_minutes = config.get("scan_interval_minutes", 5)
    logger.info(f"Scheduled file indexer to run every {interval_minutes} minutes")

    last_run = time.time()

    while True:
        try:
            # Check for manual trigger commands with a timeout
            try:
                command = _indexer_queue.get(timeout=10)
                if command == "scan":
                    logger.info("Manual indexer scan triggered")
                    _indexer_instance.start()
                    last_run = time.time()
                elif command == "stop":
                    logger.info("Stopping indexer thread")
                    break
                _indexer_queue.task_done()
            except queue.Empty:
                # No command received, check if it's time for scheduled scan
                pass

            # Check if it's time for a scheduled scan
            if time.time() - last_run > interval_minutes * 60:
                _indexer_instance.start()
                last_run = time.time()

            time.sleep(1)  # Sleep to avoid busy-waiting
        except Exception as e:
            logger.error(f"Error in indexer worker: {str(e)}")
            time.sleep(5)  # Sleep longer on errors


def register_enricher_callback(callback):
    """Register a callback to be called when the indexer finds changes"""
    global _enricher_trigger_callback
    _enricher_trigger_callback = callback
    logger.info("Registered enricher callback with indexer")


def trigger_scan():
    """Manually trigger a scan (can be called from other modules)"""
    if _indexer_thread and _indexer_thread.is_alive():
        logger.info("Triggering manual indexer scan")
        _indexer_queue.put("scan")
        return True
    else:
        logger.warning("Cannot trigger scan - indexer thread not running")
        return False


def start_indexer(config, db_manager):
    """Start the indexer process in a background thread"""
    global _indexer_thread

    # Ensure we don't start multiple indexer threads
    if _indexer_thread and _indexer_thread.is_alive():
        logger.warning("Indexer thread already running, not starting another")
        return _indexer_thread

    # Create directories if they don't exist
    os.makedirs(os.path.dirname(config["db_path"]), exist_ok=True)
    os.makedirs(config["artwork_path"], exist_ok=True)

    # Start the worker thread
    _indexer_thread = threading.Thread(
        target=_indexer_worker,
        args=(config, db_manager),
        daemon=True,
    )
    _indexer_thread.name = "LocalFiles-Indexer"
    _indexer_thread.start()

    logger.info("Started file indexer background thread")
    return _indexer_thread


def stop_indexer():
    """Stop the indexer thread"""
    if _indexer_thread and _indexer_thread.is_alive():
        logger.info("Sending stop command to indexer thread")
        _indexer_queue.put("stop")
        return True
    return False
