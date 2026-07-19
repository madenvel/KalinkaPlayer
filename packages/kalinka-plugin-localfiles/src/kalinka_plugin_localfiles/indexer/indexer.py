#!/usr/bin/env python3
import os
import re
import sys
import io
import time
import logging
import asyncio
import mimetypes
import multiprocessing
from typing import Any, Dict, List, Optional, Set, Tuple

from pathlib import Path

from PIL import Image

from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError

try:
    from inotify_simple import INotify, flags

    HAS_INOTIFY = True
except ImportError:
    HAS_INOTIFY = False

from ..config_model import LocalFilesConfig
from ..utils.name_utils import (
    album_folder_for_path,
    clean_display_name,
    expand_music_folders,
    path_within_roots,
)
from ..worker_utils import set_proc_title
from .id_generator import (
    generate_artist_id,
    generate_album_id,
    generate_track_id,
)
from .indexer_db import AsyncIndexerDb


# V/A-compilation classification (thresholds, folder heuristics, title
# helpers) lives in clustering.classify so the planner and this pass share it.
from ..clustering.classify import (  # noqa: E402
    GENERIC_FOLDER_RE as _GENERIC_FOLDER_RE,
    VA_MIN_ARTIST_UNIQUENESS,
    VA_MIN_DISTINCT_ARTISTS,
    VARIOUS_ARTISTS_ID,
    compilation_title as _compilation_title,
    strip_artist_prefix as _strip_artist_prefix,
)


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
        # Expand user (~) and resolve absolute paths for music folders. This is
        # the access boundary: only files under one of these are indexed, and
        # cleanup_stale_tracks purges anything that falls outside them.
        self.music_folders = expand_music_folders(config.music_folders)
        self.artwork_path = Path(config.artwork_path).expanduser().resolve()
        self.running = False
        self.lock = asyncio.Lock()
        # Scan progress (full scans only): total from the pre-count walk,
        # processed incremented per file. Published to the indexer_state
        # table in batches so /indexer/status can show an "indexing" stage.
        # _scan_active gates publishing — scan_folder also runs for inotify
        # dir-added batches, where there is no meaningful total.
        self._scan_active = False
        self._scan_total = 0
        self._scan_processed = 0
        self._scan_progress_written_at = 0.0

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

        # Publish a zeroed row first: it overwrites anything a scan killed
        # harder than `finally` (OOM, power loss) left behind, and an
        # all-zero row reads as "no outstanding work" while the pre-count
        # below runs.
        self._scan_total = 0
        self._scan_processed = 0
        self._scan_active = True
        await self._publish_scan_progress(force=True)

        # Pre-count pass: a directory-listing-only walk (no stat, no reads)
        # so the per-file loop below can report real percentage progress.
        self._scan_total = await self._count_supported_files()
        await self._publish_scan_progress(force=True)

        try:
            for folder in self.music_folders:
                if not os.path.exists(folder):
                    logger.warning(f"Music folder does not exist: {folder}")
                    continue

                logger.debug(f"Scanning folder: {folder}")
                await self.scan_folder(folder, changed_items)
        finally:
            # Mark the scan inactive even on failure so the status reader
            # never shows a stuck "indexing" stage.
            self._scan_active = False
            await self._publish_scan_progress(force=True)

        # Delete stale entries after scanning but before enrichment
        cleanup_results = await self.cleanup_stale_tracks()

        # If we removed any tracks, ensure we don't trigger enrichment for them
        if cleanup_results["tracks"] > 0:
            logger.info(
                "Removed stale tracks from database, proceeding with enrichment for valid tracks only"
            )

        # Coalesce V/A folders into compilation albums (or leave generic
        # dumps loose under unknown_album). Each track keeps its real artist
        # and still surfaces under it via the orphan-tracks fallback. Runs
        # after the stale-track cleanup so it doesn't operate on rows about to
        # be removed, and before notifying the enricher so it sees the result.
        va_results = await self.orphan_va_folder_tracks()
        if va_results["folders"]:
            logger.info(
                f"V/A coalesce: {va_results['folders']} folder(s), "
                f"{va_results['tracks']} track(s) re-pointed, "
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
        """Process filesystem changes detected by the inotify watcher."""
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

            if change_type == "path_removed":
                # No per-path work: the cleanup_stale_tracks() pass at the
                # end of this batch drops database rows for files that no
                # longer exist on disk.
                logger.info(f"Path removed, cleanup scheduled: {file_path}")
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
                    if self._scan_active:
                        self._scan_processed += 1
                        await self._publish_scan_progress()

    async def _count_supported_files(self) -> int:
        """Count supported audio files across the music folders. Directory
        listing only — no per-file stat — so it stays cheap even for large
        libraries."""

        def _count() -> int:
            count = 0
            for folder in self.music_folders:
                for _, _, files in os.walk(folder):
                    count += sum(
                        1 for f in files if self._is_supported_audio_file(f)
                    )
            return count

        return await asyncio.get_running_loop().run_in_executor(None, _count)

    async def _publish_scan_progress(self, force: bool = False):
        """Write scan progress to the database, throttled to one write per
        couple of seconds unless forced (scan start/end)."""
        now = time.monotonic()
        if not force and now - self._scan_progress_written_at < 2.0:
            return
        self._scan_progress_written_at = now
        try:
            await self.db_manager.set_scan_progress(
                self._scan_total, self._scan_processed, self._scan_active
            )
        except Exception as e:
            logger.debug(f"Failed to publish scan progress: {e}")

    def _is_supported_audio_file(self, filename: str) -> bool:
        """Check if the file is a supported audio format."""
        return is_supported_audio_file(filename)

    async def process_file(self, file_path: str) -> Optional[Dict[str, Optional[str]]]:
        """Process a music file and update the database. Returns changed items IDs."""
        # Access boundary: never index a file whose canonical path escapes the
        # configured music folders — e.g. a symlink sitting under a watched
        # folder that resolves elsewhere. Enforced here, using the same
        # symlink-resolving check as cleanup_stale_tracks, so the scan and the
        # cleanup agree on what is in scope; otherwise such a file would be
        # indexed on every scan and purged again, churning the database.
        if not path_within_roots(file_path, self.music_folders):
            logger.debug(f"Skipping file outside configured folders: {file_path}")
            return None

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
        device_id = str(stat.st_dev)
        inode = str(stat.st_ino)
        # Nanosecond mtime for the failure-cache key. With second resolution a
        # broken file that gets fixed within the same integer second and keeps
        # the same size would collide on the key and never be retried. The
        # tracks table stays on second-resolution modified_time.
        mtime_ns = stat.st_mtime_ns

        existing_track = await self.db_manager.get_track_by_path(file_path)
        if (
            existing_track
            and existing_track["modified_time"] == modified_time
            and existing_track["file_size"] == file_size
        ):
            logger.debug(f"File unchanged, skipping: {file_path}")
            return None

        # Negative cache: if this exact file (same size + mtime) already
        # failed extraction, don't re-read it on every scan. A file that is
        # still being uploaded changes size/mtime between scans, so it keeps
        # a different key here and is retried until it stabilizes — only a
        # genuinely broken, unchanging file stays parked.
        failure = await self.db_manager.get_failure(file_path)
        if (
            failure
            and failure["modified_time"] == mtime_ns
            and failure["file_size"] == file_size
        ):
            logger.debug(
                f"Skipping previously failed file (unchanged, "
                f"{failure['attempts']} attempt(s)): {file_path}"
            )
            return None

        metadata = await asyncio.to_thread(self._extract_metadata, file_path)
        if not metadata:
            attempts = await self.db_manager.record_failure(
                file_path, file_size, mtime_ns, "metadata extraction failed"
            )
            logger.warning(
                f"Failed to extract metadata from {file_path} "
                f"(attempt {attempts}); will retry only if the file changes"
            )
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
            # Basic fallback only: the raw filename. The enricher's
            # FilesystemFallbackPlugin detects this (title == basename) and does
            # the smart "Artist - Title" parsing — keep that boundary intact.
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
        if existing_track is None:
            await self.db_manager.insert_track(track_data)
        else:
            # Surgical update, not INSERT OR REPLACE: track_data carries only
            # indexer-owned columns, so mbid/embeddings/mood survive. enriched
            # resets to 0, so a changed file re-enriches but reuses embeddings.
            await self.db_manager.update_track(track_id, track_data)
        changes["tracks"] = track_id

        await self.db_manager.upsert_library_file(
            track_id, file_path, file_size, modified_time, device_id, inode
        )

        art_phash = (
            await asyncio.to_thread(self._art_phash, metadata["album_art"])
            if "album_art" in metadata
            else None
        )
        await self.db_manager.upsert_track_evidence(
            track_id,
            {
                "raw_tags": metadata.get("raw_tags"),
                "stream_info": metadata.get("stream_info"),
                "art_phash": art_phash,
            },
        )

        await self.db_manager.update_album_stats(album_id)
        # Successfully indexed — drop any stale failure record (e.g. an
        # earlier partial upload that has since completed).
        await self.db_manager.clear_failure(file_path)
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
        """Extract metadata from an MP3 file.

        Errors propagate to ``_extract_metadata``, which logs them once.
        """
        mp3 = MP3(file_path)
        # A missing ID3 header is a valid, fully supported case — the
        # audio plays fine, it just has no tags. Fall back to an empty
        # tag set so the track still gets indexed (title derived from
        # the filename, Unknown Artist/Album) instead of failing.
        try:
            id3 = ID3(file_path)
        except ID3NoHeaderError:
            id3 = ID3()
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
        # Verbatim text frames (incl. TPE2 album-artist, TCMP compilation) +
        # stream info for the clusterer. Binary frames (APIC) skipped.
        metadata["raw_tags"] = {
            key: str(frame)
            for key, frame in id3.items()
            if not key.startswith("APIC")
        }
        metadata["stream_info"] = {
            "sample_rate": getattr(mp3.info, "sample_rate", None),
            "channels": getattr(mp3.info, "channels", None),
            "bitrate": getattr(mp3.info, "bitrate", None),
            "codec": "mp3",
            "encoder": str(id3["TSSE"])
            if "TSSE" in id3
            else (str(id3["TENC"]) if "TENC" in id3 else None),
        }
        return metadata

    def _extract_flac_metadata(self, file_path: str, metadata: Dict) -> Dict:
        """Extract metadata from a FLAC file.

        Errors propagate to ``_extract_metadata``, which logs them once.
        """
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
        # Every Vorbis comment verbatim (incl. albumartist/compilation) +
        # stream info. Keys can repeat, so values are lists.
        metadata["raw_tags"] = {key: list(flac[key]) for key in flac.keys()}
        metadata["stream_info"] = {
            "sample_rate": getattr(flac.info, "sample_rate", None),
            "bits_per_sample": getattr(flac.info, "bits_per_sample", None),
            "channels": getattr(flac.info, "channels", None),
            "codec": "flac",
            "encoder": flac["encoder"][0] if "encoder" in flac else None,
        }
        return metadata

    @staticmethod
    def _art_phash(image_data: bytes) -> Optional[str]:
        """64-bit row-difference hash (dHash) of embedded art, 16 hex or None.

        Adjacent-pixel brightness on a 9x8 grayscale downscale. Same cover ->
        small Hamming distance; robust to re-encoding/resize.
        """
        try:
            img = Image.open(io.BytesIO(image_data)).convert("L").resize(
                (9, 8), Image.Resampling.LANCZOS
            )
            px = img.tobytes()  # 72 bytes, one grayscale value per pixel
            bits = 0
            for row in range(8):
                for col in range(8):
                    left = px[row * 9 + col]
                    right = px[row * 9 + col + 1]
                    bits = (bits << 1) | (1 if left > right else 0)
            return f"{bits:016x}"
        except Exception as e:
            logger.debug("art phash failed: %s", e)
            return None

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
        """Coalesce a V/A folder's per-track albums into one compilation album.

        Context: the indexer creates one album row per
        ``(album_folder, normalized_title)`` pair. In a V/A folder
        where each track carries its own album tag (e.g. a Jamendo
        playlist), that produces N single-track albums anchored to N
        different artists — a noisy mess in the album list.

        Behaviour: a qualifying compilation folder is collapsed into a
        single album anchored to the ``various_artists`` sentinel and
        titled after the folder (a ``VA -`` prefix is stripped). Each
        track keeps its real ``artist_id`` and still surfaces under
        that artist via ``LocalFilesInputModuleDb.get_artist_orphan_tracks``,
        which returns tracks whose ``album.artist_id != track.artist_id``
        — so the artist-navigation regression that sank an earlier
        umbrella-album attempt no longer applies.

        Two exceptions to the Various-Artists anchoring:
          * A generic dumping ground (a top-level ``music`` dir, a
            personal ``90s Mixes`` pile — see ``_compilation_title``) is
            *not* a real compilation: its tracks are left loose under
            ``unknown_album`` and surface under their artists the same way,
            without inventing a junk album.
          * A folder under a real artist's directory whose tracks are
            remixer-credited (e.g. ``.../Netsky/Remixes``) is that
            artist's own release, so the album is anchored to *them*, not
            Various Artists — see ``_parent_artist_for_folder``.
        The orphaned per-track albums are removed by the cleanup pass.

        Detection criterion is unchanged: a folder qualifies when
        its tracks span ≥``VA_MIN_DISTINCT_ARTISTS`` real artists
        AND the unique-artist-per-track ratio is ≥
        ``VA_MIN_ARTIST_UNIQUENESS``. That keeps mistagging artifacts
        (Abbey Road with 2-3 wrong-artist tags out of 17) from
        flipping to V/A.

        Returns counts of (coalesced_folders, repointed_tracks,
        deleted_orphans).
        """
        tracks = await self.db_manager.get_all_tracks()
        if not tracks:
            return {"folders": 0, "tracks": 0, "orphans": 0}

        # Group tracks by their album folder, and track which folders each
        # artist appears in (used to tell a real artist's remix album from a
        # genuine various-artists compilation).
        folder_tracks: Dict[str, List[Dict]] = {}
        artist_folders: Dict[str, Set[str]] = {}
        for t in tracks:
            folder = album_folder_for_path(t.get("file_path") or "")
            if not folder:
                continue
            folder_tracks.setdefault(folder, []).append(t)
            aid = t.get("artist_id")
            if aid:
                artist_folders.setdefault(aid, set()).add(folder)

        coalesced_folders = 0
        repointed_tracks = 0
        va_seeded = False  # create the Various-Artists sentinel at most once
        for folder, ts in folder_tracks.items():
            distinct_artists = {t["artist_id"] for t in ts if t.get("artist_id")}
            distinct_artists.discard("unknown_artist")
            n_artists = len(distinct_artists)
            n_tracks = len(ts)
            if n_artists < VA_MIN_DISTINCT_ARTISTS:
                continue
            if (n_artists / n_tracks) < VA_MIN_ARTIST_UNIQUENESS:
                continue

            # Decide where the folder's tracks go:
            #   * generic dump        -> stay loose under unknown_album
            #   * folder under a real artist (e.g. ".../Netsky/Remixes") whose
            #     tracks are remixer-credited -> that artist's album (so it
            #     doesn't masquerade as a Various-Artists compilation)
            #   * otherwise           -> a Various-Artists compilation album
            comp_title = _compilation_title(folder)
            album_reanchored = False
            if comp_title is None:
                target_id = "unknown_album"
                dest = "unknown_album"
            else:
                parent_artist = await self._parent_artist_for_folder(
                    folder, artist_folders
                )
                if parent_artist is not None:
                    owner_id = parent_artist["id"]
                    display_title = _strip_artist_prefix(
                        comp_title, parent_artist["name"]
                    )
                    dest = f"album '{display_title}' under {parent_artist['name']}"
                else:
                    owner_id = VARIOUS_ARTISTS_ID
                    display_title = comp_title
                    dest = f"compilation '{display_title}'"
                    if not va_seeded:
                        await self._ensure_various_artists()
                        va_seeded = True
                # Key the id on the title we actually store, so it honours the
                # generate_album_id (folder, normalized_title) invariant and
                # stays stable across re-scans.
                target_id = generate_album_id(display_title, folder)
                album_reanchored = await self._ensure_compilation_album(
                    target_id, display_title, owner_id
                )

            folder_repointed = 0
            for t in ts:
                # Re-point against target_id (not just "!= unknown_album") so
                # this also heals DBs detached by the older behaviour.
                if t["album_id"] != target_id:
                    await self.db_manager.update_track(t["id"], {"album_id": target_id})
                    folder_repointed += 1

            # A folder counts as coalesced when tracks moved OR an existing
            # shared-tag album was re-anchored to Various Artists in place
            # (no re-point needed, but still real work worth reporting).
            if folder_repointed or album_reanchored:
                coalesced_folders += 1
                repointed_tracks += folder_repointed
                # The tracks now belong to target_id; recompute its
                # track_count/duration (the compilation album was created
                # without them, and any source albums are about to be deleted).
                if target_id != "unknown_album":
                    await self.db_manager.update_album_stats(target_id)
                if folder_repointed:
                    logger.info(
                        f"V/A folder '{folder}' ({n_tracks} tracks, "
                        f"{n_artists} artists): {folder_repointed} track(s) -> {dest}"
                    )
                else:
                    logger.info(
                        f"V/A folder '{folder}' ({n_tracks} tracks, "
                        f"{n_artists} artists): re-anchored album -> {dest}"
                    )
            else:
                # Already coalesced on a prior scan; idempotent no-op.
                logger.debug(
                    f"V/A folder '{folder}' already coalesced "
                    f"({n_tracks} tracks, {n_artists} artists)"
                )

        deleted_albums = 0
        if repointed_tracks > 0:
            deleted_albums, _ = (
                await self.db_manager.delete_orphaned_albums_and_artists()
            )

        return {
            "folders": coalesced_folders,
            "tracks": repointed_tracks,
            "orphans": deleted_albums,
        }

    async def _ensure_various_artists(self) -> None:
        """Seed the Various-Artists sentinel artist (compilation albums hang
        off it; real per-track artists are preserved on the tracks)."""
        if not await self.db_manager.get_artist_by_id(VARIOUS_ARTISTS_ID):
            await self.db_manager.insert_artist(
                {
                    "id": VARIOUS_ARTISTS_ID,
                    "name": "Various Artists",
                    "enriched": 0,
                    "last_updated": int(time.time()),
                }
            )

    async def _parent_artist_for_folder(
        self, folder: str, artist_folders: Dict[str, Set[str]]
    ) -> Optional[Dict]:
        """Return the artist that owns ``folder``'s parent directory when it's
        a real artist with a catalog *outside* this folder — i.e. the folder
        is that artist's own remix/mix release, not a true compilation.

        Returns None for generic parents (``music`` etc.) and for netlabels
        whose only content is this folder (they have no tracks elsewhere).
        """
        parent_name = os.path.basename(os.path.dirname(folder)).strip()
        if not parent_name or _GENERIC_FOLDER_RE.match(parent_name):
            return None
        artist_id = generate_artist_id(clean_display_name(parent_name) or parent_name)
        if not (artist_folders.get(artist_id, set()) - {folder}):
            return None
        return await self.db_manager.get_artist_by_id(artist_id)

    async def _ensure_compilation_album(
        self, album_id: str, title: str, artist_id: str
    ) -> bool:
        """Create the compilation album, or re-anchor an existing same-id album
        (process_file may have created it under the first track's artist with a
        different title).

        Returns True if the album was created or re-anchored this call, False if
        it already matched (so a re-scan can tell real work from a no-op).
        """
        existing = await self.db_manager.get_album_by_id(album_id)
        if not existing:
            await self.db_manager.insert_album(
                {
                    "id": album_id,
                    "title": title,
                    "artist_id": artist_id,
                    "enriched": 0,
                    "last_updated": int(time.time()),
                }
            )
            return True
        if existing.get("artist_id") != artist_id or existing.get("title") != title:
            await self.db_manager.update_album(
                album_id,
                {
                    "artist_id": artist_id,
                    "title": title,
                    "last_updated": int(time.time()),
                },
            )
            return True
        return False

    async def cleanup_stale_tracks(self) -> Dict[str, int]:
        """Remove entries that are no longer valid for the file system.

        A track is dropped when its file either no longer exists, or falls
        outside the currently configured music folders. The latter handles a
        changed folder config: when a parent folder is removed from the
        config, its files are no longer accessible to this module and must be
        purged so they aren't served or played. Because ``run_scan`` (and thus
        this method) runs on startup, the cleanup happens immediately after a
        restart with the new config.
        """
        logger.debug("Checking for stale files in the database...")
        all_tracks = await self.db_manager.get_all_tracks()
        removed_tracks = 0
        for track in all_tracks:
            file_path = track["file_path"]
            if not os.path.exists(file_path):
                logger.info(
                    f"File no longer exists, removing from database: {file_path}"
                )
                await self.db_manager.delete_track(track["id"])
                removed_tracks += 1
            elif not path_within_roots(file_path, self.music_folders):
                logger.info(
                    "File is outside the configured music folders, removing "
                    f"from database: {file_path}"
                )
                await self.db_manager.delete_track(track["id"])
                removed_tracks += 1

        # Drop failure-cache rows for files that have since been deleted or
        # moved out of the configured folders, so the cache doesn't accumulate
        # entries for files this module no longer manages.
        for failed_path in await self.db_manager.get_failure_paths():
            if not os.path.exists(failed_path) or not path_within_roots(
                failed_path, self.music_folders
            ):
                await self.db_manager.clear_failure(failed_path)

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
    """Background worker task for real-time filesystem monitoring via inotify.

    Watches for content arriving (files written or moved in, directories
    created or moved in — a moved-in tree emits no per-file events, so it
    is scanned as a unit) and content leaving (files/directories deleted
    or moved out, which schedules the stale-track cleanup).
    """
    global _indexer_queue, _file_watcher_stop_event

    if not HAS_INOTIFY:
        logger.warning(
            "inotify_simple not available, file watcher disabled. "
            "Install with: pip install inotify_simple"
        )
        return

    try:
        music_folders = expand_music_folders(config.music_folders)
        logger.info(f"Starting file watcher for folders: {music_folders}")

        try:
            inotify = INotify()
            watched_dirs = {}

            # Watch mask: CLOSE_WRITE (file closed after write), MOVED_TO
            # (files/dirs renamed or moved in), CREATE (new files/dirs),
            # DELETE + MOVED_FROM (files/dirs deleted or moved out),
            # DELETE_SELF (watched dir removed), UNMOUNT (fs unmounted)
            watch_mask = (
                flags.CLOSE_WRITE
                | flags.MOVED_TO
                | flags.CREATE
                | flags.DELETE
                | flags.MOVED_FROM
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

                        # A directory appeared — created in place (mkdir,
                        # cp -r) or moved in whole (mv). A moved-in tree is
                        # already populated and emits no per-file events, so
                        # it must be watched AND queued for scanning as a
                        # unit. Same handling for a rename within the tree:
                        # re-adding the watches refreshes the wd -> path
                        # mapping for the new location.
                        if event.mask & (
                            flags.CREATE | flags.MOVED_TO
                        ) and os.path.isdir(file_path):
                            relevant_changes.add(("dir_added", file_path))
                            _add_watches(file_path)
                            logger.debug(f"New directory detected: {file_path}")

                        # Handle file close after write (primary trigger for files)
                        elif event.mask & flags.CLOSE_WRITE:
                            if is_supported_audio_file(file_path):
                                relevant_changes.add(("file_closed", file_path))
                                logger.debug(f"File closed after write: {file_path}")

                        # Handle file renames (uploaded as .tmp then renamed to .mp3/.flac)
                        elif event.mask & flags.MOVED_TO:
                            if is_supported_audio_file(file_path):
                                relevant_changes.add(("file_moved", file_path))
                                logger.debug(f"File moved (atomic rename): {file_path}")

                        # A file or directory left the tree (deleted or
                        # moved out). Queue the batch so the stale-track
                        # cleanup drops its database rows; for a directory,
                        # also retire the subtree's now-stale watches.
                        elif event.mask & (flags.DELETE | flags.MOVED_FROM):
                            if event.mask & flags.ISDIR:
                                prefix = file_path + os.sep
                                for wd, path in list(watched_dirs.items()):
                                    if path == file_path or path.startswith(prefix):
                                        del watched_dirs[wd]
                                        try:
                                            inotify.rm_watch(wd)
                                        except OSError:
                                            pass  # watch already gone
                                relevant_changes.add(("path_removed", file_path))
                                logger.debug(f"Directory removed/moved out: {file_path}")
                            elif is_supported_audio_file(file_path):
                                relevant_changes.add(("path_removed", file_path))
                                logger.debug(f"File removed/moved out: {file_path}")

                        # Handle watched directory removal
                        elif event.mask & flags.DELETE_SELF:
                            if event.wd in watched_dirs:
                                del watched_dirs[event.wd]
                                logger.debug(f"Directory removed: {dir_path}")

                    if relevant_changes:
                        logger.info(
                            f"File watcher detected {len(relevant_changes)} relevant events (added/changed/removed)."
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
    if not config.file_watch_enabled:
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
