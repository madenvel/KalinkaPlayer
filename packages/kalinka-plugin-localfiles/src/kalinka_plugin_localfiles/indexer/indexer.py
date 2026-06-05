#!/usr/bin/env python3
import os
import sys
import io
import time
import logging
import asyncio
import mimetypes
import multiprocessing
from typing import Any, Dict, Optional, Set, Tuple

from pathlib import Path

from PIL import Image

from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.id3 import ID3

try:
    from inotify_simple import INotify, flags

    HAS_INOTIFY = True
except ImportError:
    HAS_INOTIFY = False

from ..config_model import LocalFilesConfig
from ..utils.name_utils import album_folder_for_path, clean_display_name
from ..worker_utils import set_proc_title
from .id_generator import (
    generate_artist_id,
    generate_album_id,
    generate_track_id,
)
from .indexer_db import AsyncIndexerDb


# A folder's tracks coalesce into a single V/A compilation album when:
#   1. they span at least this many distinct artists, AND
#   2. at least this fraction of tracks have unique artists.
# Both have to be true so we don't misfire on mistagged albums (e.g.
# Abbey Road with 2-3 wrong-artist tags out of 17 tracks).
VA_MIN_DISTINCT_ARTISTS = 4
VA_MIN_ARTIST_UNIQUENESS = 0.5


SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".flac"}


# Configure logger for watchfiles.main only to WARNING level
watchfiles_logger = logging.getLogger("watchfiles.main")
watchfiles_logger.setLevel(logging.WARNING)
aiosqlite_logger = logging.getLogger("aiosqlite")
aiosqlite_logger.setLevel(logging.WARNING)

logger = logging.getLogger("indexer")


# Global variables to manage indexer state
_indexer_task: Optional[asyncio.Task] = None
_indexer_queue: asyncio.Queue = asyncio.Queue()
_file_watcher_task: Optional[asyncio.Task] = None
_file_watcher_stop_event: asyncio.Event = asyncio.Event()
_shutdown_event = asyncio.Event()
_enricher_queue: Optional[multiprocessing.Queue] = None


async def trigger_enricher_update(data):
    """Trigger the enricher update with the given data"""
    if not data:
        logger.warning("No data provided to trigger enricher update")
        return

    logger.debug(f"Triggering enricher update with data: {data}")

    if _enricher_queue is None:
        logger.error("Enricher queue is not initialized; cannot trigger update")
        return

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, lambda: _enricher_queue.put(data, block=True, timeout=30.0)
    )


def is_supported_audio_file(filename: str) -> bool:
    """Check if the file is a supported audio format."""
    ext = os.path.splitext(filename.lower())[1]
    return ext in SUPPORTED_AUDIO_EXTENSIONS


class FileIndexer:
    def __init__(self, config: LocalFilesConfig, db_manager: AsyncIndexerDb):
        self.config = config
        self.db_manager = db_manager
        # Expand user (~) and resolve absolute paths for music folders
        self.music_folders = [
            str(Path(folder).expanduser().resolve()) for folder in config.music_folders
        ]
        self.artwork_path = Path(config.artwork_path).expanduser().resolve()
        self.running = False
        self.lock = asyncio.Lock()

    async def start(self):
        """Start the indexer process"""
        async with self.lock:
            if self.running:
                logger.warning("Indexer already running, skipping")
                return
            self.running = True

        try:
            await self.run_scan()
            logger.debug("Indexer scan completed")
        except Exception as e:
            logger.exception(f"Error running indexer scan: {str(e)}")
        finally:
            async with self.lock:
                self.running = False

    async def run_scan(self):
        """Scan all music folders for files"""
        logger.debug(f"Starting music file scan in folders: {self.music_folders}")

        changed_items: Dict[str, Set[str]] = {
            "artists": set(),
            "albums": set(),
            "tracks": set(),
        }

        for folder in self.music_folders:
            if not os.path.exists(folder):
                logger.warning(f"Music folder does not exist: {folder}")
                continue

            logger.debug(f"Scanning folder: {folder}")
            await self.scan_folder(folder, changed_items)

        # Delete stale entries after scanning but before enrichment
        cleanup_results = await self.cleanup_stale_tracks()

        # If we removed any tracks, ensure we don't trigger enrichment for them
        if cleanup_results["tracks"] > 0:
            logger.info(
                "Removed stale tracks from database, proceeding with enrichment for valid tracks only"
            )

        # Detach tracks in V/A folders so each track surfaces as a
        # single under its real artist instead of cluttering the
        # album list with one-track-per-artist rows. Runs after the
        # stale-track cleanup so it doesn't operate on rows about to
        # be removed, and before notifying the enricher so it sees
        # the post-detach shape.
        va_results = await self.orphan_va_folder_tracks()
        if va_results["folders"]:
            logger.info(
                f"V/A folder detach: {va_results['folders']} folder(s), "
                f"{va_results['tracks']} track(s) re-pointed to unknown_album, "
                f"{va_results['orphans']} orphan album(s) deleted"
            )

        # If anything changed and we have an enricher callback, notify it
        if any(changed_items.values()):
            logger.info(
                f"Scan completed with changes: Artists={len(changed_items['artists'])}, "
                f"Albums={len(changed_items['albums'])}, Tracks={len(changed_items['tracks'])}"
            )
        else:
            logger.debug("Scan completed with no changes")

        # Trigger the enricher run regardless of changes
        # as there might be old files pending enrichment
        await trigger_enricher_update("enrich")

    async def handle_incremental_changes(self, changes: Set[Tuple[str, str]]):
        """Process file changes detected by inotify (CLOSE_WRITE/MOVED_TO events)"""
        changed_items: Dict[str, Set[str]] = {
            "artists": set(),
            "albums": set(),
            "tracks": set(),
        }

        processed_dirs = set()
        processed_files = set()

        for change_type, file_path in changes:
            logger.debug(f"Change detected: {change_type} - {file_path}")

            if change_type == "dir_added":
                logger.info(f"New directory detected: {file_path}")
                try:
                    await self.scan_folder(file_path, changed_items)
                    processed_dirs.add(file_path)
                    continue
                except Exception as e:
                    logger.exception(
                        f"Error scanning new directory {file_path}: {str(e)}"
                    )
                    continue

            if file_path in processed_files:
                continue

            parent_processed = False
            for processed_dir in processed_dirs:
                if file_path.startswith(processed_dir + os.sep):
                    logger.debug(
                        f"Skipping file in already processed directory: {file_path}"
                    )
                    parent_processed = True
                    break
            if parent_processed:
                continue

            if not self._is_supported_audio_file(file_path):
                continue

            if change_type in ("file_closed", "file_moved"):
                # File was closed after write or moved (atomic rename) - safe to process
                try:
                    logger.info(f"Processing changed file: {file_path}")
                    result_changes = await self.process_file(file_path)
                    processed_files.add(file_path)
                    if result_changes:
                        for key, value in result_changes.items():
                            if value:
                                changed_items[key].add(value)
                except Exception as e:
                    logger.exception(
                        f"Error processing changed file {file_path}: {str(e)}"
                    )

        await self.cleanup_stale_tracks()

        if any(changed_items.values()):
            logger.info(
                f"File changes detected: Artists={len(changed_items['artists'])}, "
                f"Albums={len(changed_items['albums'])}, Tracks={len(changed_items['tracks'])}"
            )
            await trigger_enricher_update("enrich")

    async def scan_folder(self, folder: str, changed_items: Dict[str, Set[str]]):
        """Recursively scan a folder for music files"""
        for root, _, files in os.walk(folder):
            for file in files:
                if self._is_supported_audio_file(file):
                    file_path = os.path.join(root, file)
                    try:
                        result_changes = await self.process_file(file_path)
                        if result_changes:
                            for key, value in result_changes.items():
                                if value:
                                    changed_items[key].add(value)
                    except Exception as e:
                        logger.exception(f"Error processing file {file_path}: {str(e)}")

    def _is_supported_audio_file(self, filename: str) -> bool:
        """Check if the file is a supported audio format."""
        return is_supported_audio_file(filename)

    async def process_file(self, file_path: str) -> Optional[Dict[str, Optional[str]]]:
        """Process a music file and update the database. Returns changed items IDs."""
        try:
            stat = os.stat(file_path)
        except FileNotFoundError:
            logger.debug(f"File disappeared before processing: {file_path}")
            return None

        # Quiescence guard: skip files that may still be in mid-upload.
        # POSIX has no "upload complete" signal, so we infer it from mtime/size
        # stability over a short window. If the file is still changing, defer —
        # the next CLOSE_WRITE/MOVED_TO event or scheduled rescan will retry it.
        quiescence_seconds = self.config.quiescence_seconds
        if quiescence_seconds > 0:
            age = time.time() - stat.st_mtime
            if age < quiescence_seconds:
                await asyncio.sleep(quiescence_seconds - age)
                try:
                    stat_after = os.stat(file_path)
                except FileNotFoundError:
                    logger.debug(
                        f"File disappeared during quiescence wait: {file_path}"
                    )
                    return None
                if (
                    stat_after.st_size != stat.st_size
                    or stat_after.st_mtime != stat.st_mtime
                ):
                    logger.info(
                        f"File still being written, deferring: {file_path} "
                        f"(size {stat.st_size}->{stat_after.st_size}, "
                        f"mtime {stat.st_mtime}->{stat_after.st_mtime})"
                    )
                    return None
                stat = stat_after

        file_size = stat.st_size
        modified_time = int(stat.st_mtime)

        existing_track = await self.db_manager.get_track_by_path(file_path)
        if (
            existing_track
            and existing_track["modified_time"] == modified_time
            and existing_track["file_size"] == file_size
        ):
            logger.debug(f"File unchanged, skipping: {file_path}")
            return None

        metadata = await asyncio.to_thread(self._extract_metadata, file_path)
        if not metadata:
            logger.warning(f"Failed to extract metadata from {file_path}")
            return None

        changes: Dict[str, Optional[str]] = {
            "artists": None,
            "albums": None,
            "tracks": None,
        }

        metadata["file_path"] = file_path
        metadata["file_size"] = file_size
        metadata["modified_time"] = modified_time
        metadata["last_updated"] = int(time.time())

        raw_artist = metadata.get("artist", "Unknown Artist")
        artist_name = clean_display_name(raw_artist) or "Unknown Artist"
        artist_id = generate_artist_id(artist_name)

        artist = await self.db_manager.get_artist_by_id(artist_id)
        if not artist:
            await self.db_manager.insert_artist(
                {
                    "id": artist_id,
                    "name": artist_name,
                    "enriched": 0,
                    "last_updated": int(time.time()),
                }
            )
            changes["artists"] = artist_id

        raw_album = metadata.get("album", "Unknown Album")
        album_title = clean_display_name(raw_album) or "Unknown Album"
        album_id = generate_album_id(album_title, album_folder_for_path(file_path))

        album = await self.db_manager.get_album_by_id(album_id)
        if not album:
            album_data: Dict[str, Any] = {
                "id": album_id,
                "title": album_title,
                "artist_id": artist_id,
                "enriched": 0,
                "last_updated": int(time.time()),
            }
            if "year" in metadata:
                album_data["year"] = metadata["year"]
            if "genre" in metadata:
                album_data["genre"] = metadata["genre"]
            if "album_art" in metadata:
                image_url_filename = f"{album_id}.jpg"
                await asyncio.to_thread(
                    self._save_images, metadata["album_art"], album_id, "album"
                )
                album_data["image_url"] = image_url_filename
            await self.db_manager.insert_album(album_data)
            changes["albums"] = album_id

        track_id = generate_track_id(file_path)
        track_data: Dict[str, Any] = {
            "id": track_id,
            "title": metadata.get("title", os.path.basename(file_path)),
            "album_id": album_id,
            "artist_id": artist_id,
            "duration": metadata.get("duration", 0),
            "track_number": metadata.get("track_number"),
            "disc_number": metadata.get("disc_number"),
            "file_path": file_path,
            "format": metadata.get("format", "unknown"),
            "file_size": file_size,
            "modified_time": modified_time,
            "replaygain_peak": metadata.get("replaygain_peak"),
            "replaygain_gain": metadata.get("replaygain_gain"),
            "enriched": 0,
            "last_updated": int(time.time()),
        }
        await self.db_manager.insert_track(track_data)
        changes["tracks"] = track_id

        await self.db_manager.update_album_stats(album_id)
        logger.debug(f"Processed file: {file_path}")
        return changes

    def _extract_metadata(self, file_path: str) -> Optional[Dict]:
        """Extract metadata from a music file"""
        try:
            mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            if mime_type == "audio/x-flac":
                mime_type = "audio/flac"

            metadata = {
                "format": mime_type,
            }
            if "audio/mpeg" in mime_type:
                return self._extract_mp3_metadata(file_path, metadata)
            elif "audio/flac" in mime_type:
                return self._extract_flac_metadata(file_path, metadata)
            else:
                logger.warning(
                    f"Unsupported file format: {file_path}, format: {mime_type}"
                )
                return None
        except Exception as e:
            logger.exception(f"Error extracting metadata from {file_path}: {str(e)}")
            return None

    def _extract_mp3_metadata(self, file_path: str, metadata: Dict) -> Dict:
        """Extract metadata from an MP3 file"""
        try:
            mp3 = MP3(file_path)
            id3 = ID3(file_path)
            metadata["duration"] = int(mp3.info.length)
            if "TIT2" in id3:
                metadata["title"] = str(id3["TIT2"])
            if "TPE1" in id3:
                metadata["artist"] = str(id3["TPE1"])
            if "TALB" in id3:
                metadata["album"] = str(id3["TALB"])
            if "TRCK" in id3:
                track_str = str(id3["TRCK"])
                if "/" in track_str:
                    track_str = track_str.split("/")[0]
                try:
                    metadata["track_number"] = int(track_str)
                except ValueError:
                    pass
            if "TPOS" in id3:
                disc_str = str(id3["TPOS"])
                if "/" in disc_str:
                    disc_str = disc_str.split("/")[0]
                try:
                    metadata["disc_number"] = int(disc_str)
                except ValueError:
                    pass
            if "TDRC" in id3:
                try:
                    metadata["year"] = int(str(id3["TDRC"]).split("-")[0])
                except (ValueError, IndexError):
                    pass
            if "TCON" in id3:
                metadata["genre"] = str(id3["TCON"])
            if "TXXX:replaygain_track_gain" in id3:
                gain_str = str(id3["TXXX:replaygain_track_gain"])
                try:
                    metadata["replaygain_gain"] = float(gain_str.replace(" dB", ""))
                except ValueError:
                    pass
            if "TXXX:replaygain_track_peak" in id3:
                peak_str = str(id3["TXXX:replaygain_track_peak"])
                try:
                    metadata["replaygain_peak"] = float(peak_str)
                except ValueError:
                    pass
            for tag in ["APIC:", "APIC:Cover", "APIC:CoverFront"]:
                if tag in id3:
                    apic = id3[tag]
                    metadata["album_art"] = apic.data
                    break
            return metadata
        except Exception as e:
            logger.exception(
                f"Error extracting MP3 metadata from {file_path}: {str(e)}"
            )
            raise

    def _extract_flac_metadata(self, file_path: str, metadata: Dict) -> Dict:
        """Extract metadata from a FLAC file"""
        try:
            flac = FLAC(file_path)
            metadata["duration"] = int(flac.info.length)
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
            if "discnumber" in flac:
                disc_str = flac["discnumber"][0]
                if "/" in disc_str:
                    disc_str = disc_str.split("/")[0]
                try:
                    metadata["disc_number"] = int(disc_str)
                except ValueError:
                    pass
            if "date" in flac:
                try:
                    metadata["year"] = int(flac["date"][0].split("-")[0])
                except (ValueError, IndexError):
                    pass
            if "genre" in flac:
                metadata["genre"] = flac["genre"][0]
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
            pictures = flac.pictures
            if pictures:
                for pic in pictures:
                    if pic.type == 3:  # Cover (front)
                        metadata["album_art"] = pic.data
                        break
                else:
                    metadata["album_art"] = pictures[0].data
            return metadata
        except Exception as e:
            logger.exception(
                f"Error extracting FLAC metadata from {file_path}: {str(e)}"
            )
            raise

    def _save_images(self, image_data: bytes, entity_id: str, entity_type: str):
        """Save artwork images in different sizes"""
        try:
            img = Image.open(io.BytesIO(image_data))
            dir_path = os.path.join(self.artwork_path, entity_type)
            os.makedirs(dir_path, exist_ok=True)
            if img.mode != "RGB":
                img = img.convert("RGB")

            thumbnail = img.copy()
            thumbnail.thumbnail((50, 50), Image.Resampling.LANCZOS)
            thumbnail.save(
                os.path.join(dir_path, f"{entity_id}_thumbnail.jpg"), "JPEG", quality=90
            )

            small = img.copy()
            small.thumbnail((230, 230), Image.Resampling.LANCZOS)
            small.save(
                os.path.join(dir_path, f"{entity_id}_small.jpg"), "JPEG", quality=90
            )

            large = img.copy()
            large.thumbnail((600, 600), Image.Resampling.LANCZOS)
            large.save(
                os.path.join(dir_path, f"{entity_id}_large.jpg"), "JPEG", quality=90
            )
            return True
        except Exception as e:
            logger.exception(
                f"Error saving artwork for {entity_type} {entity_id}: {str(e)}"
            )
            return False

    async def orphan_va_folder_tracks(self) -> Dict[str, int]:
        """Disassemble per-track albums in V/A folders so each track
        shows up as a single under its real artist.

        Context: the indexer creates one album row per
        ``(album_folder, normalized_title)`` pair. In a V/A folder
        where each track carries its own album tag (e.g. a Jamendo
        playlist), that produces N single-track albums anchored to N
        different artists — a noisy mess in the album list.

        An earlier version of this method created a synthetic
        Various-Artists umbrella album and re-pointed all of the
        folder's tracks at it. That cleaned up the album list but
        broke artist navigation: a track's ``artist_id`` was still
        correctly the real artist, but the album was anchored to
        ``various_artists``, so the artist page found no albums for
        them and they appeared empty. The album tag is the
        unreliable signal here; the artist tag is the reliable one.

        New behaviour: in a V/A folder we re-point every track's
        ``album_id`` to the ``unknown_album`` sentinel. The per-track
        single-track albums then have no referring tracks and are
        removed by the orphan-cleanup pass. The tracks themselves
        keep their real ``artist_id`` and surface under their artist
        via the orphan-tracks fallback in the browse view (see
        ``LocalFilesInputModuleDb.get_artist_orphan_tracks``).

        Detection criterion is unchanged: a folder qualifies when
        its tracks span ≥``VA_MIN_DISTINCT_ARTISTS`` real artists
        AND the unique-artist-per-track ratio is ≥
        ``VA_MIN_ARTIST_UNIQUENESS``. That keeps mistagging artifacts
        (Abbey Road with 2-3 wrong-artist tags out of 17) from
        flipping to V/A.

        Returns counts of (detached_folders, repointed_tracks,
        deleted_orphans).
        """
        tracks = await self.db_manager.get_all_tracks()
        if not tracks:
            return {"folders": 0, "tracks": 0, "orphans": 0}

        # Group tracks by their album folder.
        folder_tracks: Dict[str, List[Dict]] = {}
        for t in tracks:
            folder = album_folder_for_path(t.get("file_path") or "")
            if not folder:
                continue
            folder_tracks.setdefault(folder, []).append(t)

        detached_folders = 0
        repointed_tracks = 0
        for folder, ts in folder_tracks.items():
            distinct_artists = {t["artist_id"] for t in ts if t.get("artist_id")}
            distinct_artists.discard("unknown_artist")
            n_artists = len(distinct_artists)
            n_tracks = len(ts)
            if n_artists < VA_MIN_DISTINCT_ARTISTS:
                continue
            if (n_artists / n_tracks) < VA_MIN_ARTIST_UNIQUENESS:
                continue

            folder_repointed = 0
            for t in ts:
                if t["album_id"] != "unknown_album":
                    await self.db_manager.update_track(
                        t["id"], {"album_id": "unknown_album"}
                    )
                    folder_repointed += 1

            if folder_repointed:
                detached_folders += 1
                repointed_tracks += folder_repointed
                logger.info(
                    f"V/A folder '{folder}' ({n_tracks} tracks, "
                    f"{n_artists} artists): {folder_repointed} track(s) "
                    f"detached to unknown_album"
                )
            else:
                # Already detached on a prior scan; the detect pass is
                # idempotent, so don't re-announce the no-op every cycle.
                logger.debug(
                    f"V/A folder '{folder}' already detached "
                    f"({n_tracks} tracks, {n_artists} artists)"
                )

        deleted_albums = 0
        if repointed_tracks > 0:
            deleted_albums, _ = (
                await self.db_manager.delete_orphaned_albums_and_artists()
            )

        return {
            "folders": detached_folders,
            "tracks": repointed_tracks,
            "orphans": deleted_albums,
        }

    async def cleanup_stale_tracks(self) -> Dict[str, int]:
        """Remove entries for files that no longer exist in the file system"""
        logger.debug("Checking for stale files in the database...")
        all_tracks = await self.db_manager.get_all_tracks()
        removed_tracks = 0
        for track in all_tracks:
            file_path = track["file_path"]
            if not os.path.exists(file_path):
                logger.info(
                    f"File no longer exists, removing from database: {file_path}"
                )
                track_id = track["id"]
                await self.db_manager.delete_track(track_id)
                removed_tracks += 1

        removed_albums, removed_artists = (
            await self.db_manager.delete_orphaned_albums_and_artists()
        )
        if removed_tracks > 0 or removed_albums > 0 or removed_artists > 0:
            logger.info(
                f"Cleanup completed: {removed_tracks} tracks, {removed_albums} albums, "
                f"and {removed_artists} artists removed"
            )
        else:
            logger.debug("No stale entries found in the database")
        return {
            "tracks": removed_tracks,
            "albums": removed_albums,
            "artists": removed_artists,
        }


async def _indexer_worker(config: LocalFilesConfig, db_manager: AsyncIndexerDb):
    """Background worker task for the indexer"""
    global _shutdown_event, _indexer_queue

    try:
        indexer_instance = FileIndexer(config, db_manager)
        await indexer_instance.start()
        logger.info("Indexer worker started")
        interval_minutes = config.scan_interval_minutes
        logger.info(f"Scheduled file indexer to run every {interval_minutes} minutes")
        last_run = time.time()

        while True:
            try:
                try:
                    command = await asyncio.wait_for(_indexer_queue.get(), timeout=30.0)
                    logger.debug(f"Received command: {command}")
                    if command == "scan":
                        logger.info("Manual indexer scan triggered")
                        await indexer_instance.start()
                        last_run = time.time()
                    elif command == "stop":
                        logger.info("Stopping indexer worker")
                        _shutdown_event.set()
                        break
                    elif isinstance(command, dict) and "incremental_changes" in command:
                        logger.info("File watcher detected changes, processing...")
                        await indexer_instance.handle_incremental_changes(
                            command["incremental_changes"]
                        )
                        last_run = time.time()
                    _indexer_queue.task_done()
                except asyncio.TimeoutError:
                    # This is expected - allows periodic checking for scheduled scans
                    pass

                if time.time() - last_run > interval_minutes * 60:
                    logger.debug(
                        f"Scheduled indexer scan triggered (interval: {interval_minutes} mins)"
                    )
                    await indexer_instance.start()
                    last_run = time.time()

                await asyncio.sleep(1)
            except asyncio.CancelledError:
                logger.info("Indexer worker cancelled.")
                break
            except Exception as e:
                logger.exception(f"Error in indexer worker inner loop: {str(e)}")
                await asyncio.sleep(5)
    except Exception as e:
        logger.exception(f"Fatal error in indexer worker: {str(e)}")
        # Re-raise to ensure the task failure is visible
        raise


async def _file_watcher_worker(config: LocalFilesConfig):
    """Background worker task for real-time filesystem monitoring using inotify CLOSE_WRITE events"""
    global _indexer_queue, _file_watcher_stop_event

    if not HAS_INOTIFY:
        logger.warning(
            "inotify_simple not available, file watcher disabled. "
            "Install with: pip install inotify_simple"
        )
        return

    try:
        music_folders = [
            str(Path(folder).expanduser().resolve()) for folder in config.music_folders
        ]
        logger.info(f"Starting file watcher for folders: {music_folders}")

        try:
            inotify = INotify()
            watched_dirs = {}

            # Watch mask: CLOSE_WRITE (file closed after write), MOVED_TO (atomic renames),
            # CREATE (for new dirs), DELETE_SELF (dir removed), UNMOUNT (fs unmounted)
            watch_mask = (
                flags.CLOSE_WRITE
                | flags.MOVED_TO
                | flags.CREATE
                | flags.DELETE_SELF
                | flags.UNMOUNT
            )

            def _add_watches(path: str):
                """Recursively add watches to all directories."""
                try:
                    wd = inotify.add_watch(path, watch_mask)
                    watched_dirs[wd] = path
                    logger.debug(f"Watching directory: {path}")
                    for entry in os.listdir(path):
                        subpath = os.path.join(path, entry)
                        if os.path.isdir(subpath) and not os.path.islink(subpath):
                            _add_watches(subpath)
                except (OSError, PermissionError) as e:
                    logger.debug(f"Could not watch {path}: {e}")

            for folder in music_folders:
                _add_watches(folder)

            logger.info(
                f"File watcher initialized with {len(watched_dirs)} directories"
            )

            while not _file_watcher_stop_event.is_set():
                try:
                    # inotify_simple's timeout follows select.poll() —
                    # MILLISECONDS, not seconds. timeout=1 was a busy-spin
                    # at ~1000 reads/sec (the ThreadPoolExecutor worker
                    # processing each submit() showed up as 27% CPU at
                    # idle in py-spy). 1000 ms = 1 s gives a check
                    # cadence consistent with how often shutdown needs
                    # to be observed while still letting the read block
                    # in-kernel for almost all of the time.
                    events = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: inotify.read(timeout=1000)
                    )
                    if not events:
                        continue

                    relevant_changes: Set[Tuple[str, str]] = set()  # (event_type, path)

                    for event in events:
                        dir_path = watched_dirs.get(event.wd, "")
                        if not dir_path:
                            continue

                        file_path = (
                            os.path.join(dir_path, event.name)
                            if event.name
                            else dir_path
                        )

                        # Handle new directory creation
                        if event.mask & flags.CREATE and os.path.isdir(file_path):
                            relevant_changes.add(("dir_added", file_path))
                            _add_watches(file_path)
                            logger.debug(f"New directory detected: {file_path}")

                        # Handle file close after write (primary trigger for files)
                        elif event.mask & flags.CLOSE_WRITE:
                            if is_supported_audio_file(file_path):
                                relevant_changes.add(("file_closed", file_path))
                                logger.debug(f"File closed after write: {file_path}")

                        # Handle atomic renames (uploaded as .tmp then renamed to .mp3/.flac)
                        elif event.mask & flags.MOVED_TO:
                            if is_supported_audio_file(file_path):
                                relevant_changes.add(("file_moved", file_path))
                                logger.debug(f"File moved (atomic rename): {file_path}")

                        # Handle directory removal
                        elif event.mask & flags.DELETE_SELF:
                            if event.wd in watched_dirs:
                                del watched_dirs[event.wd]
                                logger.debug(f"Directory removed: {dir_path}")

                    if relevant_changes:
                        logger.info(
                            f"File watcher detected {len(relevant_changes)} relevant events (CLOSE_WRITE/MOVED_TO)."
                        )
                        await _indexer_queue.put(
                            {"incremental_changes": relevant_changes}
                        )
                        logger.info("Changes added to indexer queue for processing.")

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.exception(f"Error reading inotify events: {e}")
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("File watcher worker cancelled.")
        finally:
            logger.info("File watcher task exited")
    except Exception as e:
        logger.exception(f"Fatal error in file watcher worker: {str(e)}")
        raise


def start_indexer(
    config: LocalFilesConfig, db_manager: AsyncIndexerDb
) -> Optional[asyncio.Task]:
    """Start the indexer process in a background task"""
    global _indexer_task
    if _indexer_task and not _indexer_task.done():
        logger.warning("Indexer task already running, not starting another")
        return _indexer_task

    artwork_path = Path(config.artwork_path).expanduser().resolve()
    db_path = Path(config.db_path).expanduser().resolve()

    db_dir = os.path.dirname(db_path)
    if db_dir:  # Only create directory if path is not empty
        os.makedirs(db_dir, exist_ok=True)
    os.makedirs(artwork_path, exist_ok=True)
    os.makedirs(
        os.path.join(artwork_path, "album"),
        exist_ok=True,
    )
    os.makedirs(
        os.path.join(artwork_path, "artist"),
        exist_ok=True,
    )

    _indexer_task = asyncio.create_task(_indexer_worker(config, db_manager))

    # Add done callback to log any unhandled exceptions
    def _on_indexer_task_done(task):
        if task.cancelled():
            logger.info("Indexer task was cancelled")
        elif task.exception():
            logger.exception(f"Indexer task failed with exception: {task.exception()}")
        else:
            logger.info("Indexer task completed normally")

    _indexer_task.add_done_callback(_on_indexer_task_done)
    logger.info("Started file indexer background task")
    return _indexer_task


async def stop_indexer() -> bool:
    """Stop the indexer task"""
    global _indexer_task, _indexer_queue
    if _indexer_task and not _indexer_task.done():
        logger.info("Sending stop command to indexer task")
        try:
            await _indexer_queue.put("stop")
            await asyncio.wait_for(_indexer_task, timeout=7.0)
            logger.info("Indexer task successfully stopped")
            return True
        except asyncio.TimeoutError:
            logger.warning("Indexer task did not stop in time, cancelling.")
            _indexer_task.cancel()
            try:
                await _indexer_task
            except asyncio.CancelledError:
                logger.info("Indexer task was cancelled.")
            return False
        except Exception as e:
            logger.exception(f"Error stopping indexer task: {e}")
            return False
    elif _indexer_task and _indexer_task.done():
        logger.info("Indexer task was already done.")
        return True
    logger.info("No active indexer task to stop.")
    return False


async def stop_file_watcher() -> bool:
    """Stop the file watcher task"""
    global _file_watcher_task, _file_watcher_stop_event
    if _file_watcher_task and not _file_watcher_task.done():
        logger.info("Sending stop signal to file watcher task")
        _file_watcher_stop_event.set()
        try:
            await asyncio.wait_for(_file_watcher_task, timeout=7.0)
            logger.info("File watcher task successfully stopped")
            return True
        except asyncio.TimeoutError:
            logger.warning("File watcher task did not stop in time, cancelling.")
            _file_watcher_task.cancel()
            try:
                await _file_watcher_task
            except asyncio.CancelledError:
                logger.info("File watcher task was cancelled.")
            return False
        except Exception as e:
            logger.exception(f"Error stopping file watcher task: {e}")
            return False
    elif _file_watcher_task and _file_watcher_task.done():
        logger.info("File watcher task was already done.")
        return True
    logger.info("No active file watcher task to stop.")
    return False


def start_file_watcher(config: LocalFilesConfig) -> Optional[asyncio.Task]:
    """Start the file watcher process in a background task if enabled"""
    global _file_watcher_task, _file_watcher_stop_event
    if config.file_watch_enabled == False:
        logger.info("File watcher is disabled in config.")
        return None

    if _file_watcher_task and not _file_watcher_task.done():
        logger.warning("File watcher task already running, not starting another")
        return _file_watcher_task

    _file_watcher_stop_event.clear()
    _file_watcher_task = asyncio.create_task(_file_watcher_worker(config))

    # Add done callback to log any unhandled exceptions
    def _on_file_watcher_task_done(task):
        if task.cancelled():
            logger.info("File watcher task was cancelled")
        elif task.exception():
            logger.exception(
                f"File watcher task failed with exception: {task.exception()}"
            )
        else:
            logger.info("File watcher task completed normally")

    _file_watcher_task.add_done_callback(_on_file_watcher_task_done)
    logger.info("Started file watcher background task")
    return _file_watcher_task


async def async_main(config: LocalFilesConfig):
    """Runs the main application logic asynchronously."""
    global _indexer_task, _file_watcher_task, _shutdown_event

    db_manager = AsyncIndexerDb(config)

    loop = asyncio.get_running_loop()

    import signal

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown_event.set)

    _indexer_task = start_indexer(config, db_manager)
    _file_watcher_task = start_file_watcher(config)

    try:
        await _shutdown_event.wait()
    except asyncio.CancelledError:
        logger.info("Server cancelled, shutting down...")
    except Exception as e:
        logger.exception(f"Error in main loop: {str(e)}")
    finally:
        logger.info("Main async runner initiating shutdown of tasks...")
        if _file_watcher_task and not _file_watcher_task.done():
            logger.info("Stopping file watcher task...")
            await stop_file_watcher()
        else:
            logger.info("File watcher task was not running or already done.")

        if _indexer_task and not _indexer_task.done():
            logger.info("Stopping indexer task...")
            await stop_indexer()
        else:
            logger.info("Indexer task was not running or already done.")

        logger.info("All background tasks processed for shutdown.")


def main(config: LocalFilesConfig, enricher_queue, logger_queue):
    """Main entry point for the indexer daemon."""

    set_proc_title("kal-indexer")

    global _enricher_queue
    _enricher_queue = enricher_queue
    try:
        import logging.handlers

        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        # Set root logger level to DEBUG to allow all logs through to the queue
        root.setLevel(logging.DEBUG)
        root.addHandler(logging.handlers.QueueHandler(logger_queue))

        asyncio.run(async_main(config))
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received by asyncio.run. Exiting.")
    except Exception as e:
        logger.critical(f"Unhandled exception in asyncio.run: {e}", exc_info=True)
    finally:
        logger.info("Indexer daemon finished.")
