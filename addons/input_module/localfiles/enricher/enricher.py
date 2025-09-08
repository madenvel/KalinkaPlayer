#!/usr/bin/env python3
import multiprocessing
import asyncio
import enum
import logging
import queue
import signal
import sys
from typing import Optional

from ..config_model import LocalFilesConfig

from .musicbrainz_plugin import MusicBrainzPlugin
from .acoustid_plugin import AcoustIdPlugin
from .wikidata_plugin import WikidataPlugin
from .deezer_plugin import DeezerPlugin
from .filesystem_fallback_plugin import FilesystemFallbackPlugin
from .enricher_db import AsyncEnricherDb

logger = logging.getLogger("enricher")

# Set MusicBrainzNGS log level to warning to reduce verbosity
musicbrainz_logger = logging.getLogger("musicbrainzngs")
musicbrainz_logger.setLevel(logging.WARNING)
aiosqlite_logger = logging.getLogger("aiosqlite")
aiosqlite_logger.setLevel(logging.WARNING)

# Required fields for each entity type
# These define what metadata fields are required for an entity to be considered fully enriched
ARTIST_REQUIRED_FIELDS = ["name", "mbid", "image_url"]
ALBUM_REQUIRED_FIELDS = ["title", "artist_id", "mbid", "image_url", "year", "genre"]
TRACK_REQUIRED_FIELDS = [
    "title",
    "artist_id",
    "album_id",
    "mbid",
    "duration",
    "track_number",
]

# Global variables to manage enricher state
_enricher_task: Optional[asyncio.Task] = None
_enricher_queue: multiprocessing.Queue = multiprocessing.Queue()
_shutdown_event = asyncio.Event()


class EnrichmentStatus(enum.IntEnum):
    """Enum for enrichment status values"""

    NOT_ENRICHED = 0  # Initial state, needs enrichment
    ENRICHED = 1  # Successfully enriched with all required fields
    FAILED = 2  # Failed enrichment, missing required fields after all plugins


class MetadataEnricher:
    """Main enricher class that processes database entries using async"""

    def __init__(self, config: LocalFilesConfig, db_manager: AsyncEnricherDb):
        self.config = config
        self.db_manager = db_manager
        self.running = False
        self.lock = asyncio.Lock()
        self.plugins = []

        logger.info("Initializing MetadataEnricher plugins...")

        # The order of plugins matters for the enrichment process
        # as the first one found a match will be used
        if config.enricher.plugins.acoustid.enabled:
            logger.info("Loading AcoustIdPlugin")
            self.plugins.append(AcoustIdPlugin(config, self.db_manager))

        # Filesystem fallback plugin - always enabled and runs last
        # This provides basic metadata extraction from file paths when other plugins fail
        if config.enricher.plugins.filesystem_fallback_enabled:
            logger.info("Loading FilesystemFallbackPlugin")
            self.plugins.append(FilesystemFallbackPlugin(config, self.db_manager))

        if config.enricher.plugins.musicbrainz.enabled:
            logger.info("Loading MusicBrainzPlugin")
            self.plugins.append(MusicBrainzPlugin(config, self.db_manager))

        if config.enricher.plugins.wikidata.enabled:
            logger.info("Loading WikidataPlugin")
            self.plugins.append(WikidataPlugin(config, self.db_manager))

        if config.enricher.plugins.deezer.enabled:
            logger.info("Loading DeezerPlugin")
            self.plugins.append(DeezerPlugin(config, self.db_manager))

        logger.info(
            f"Loaded {len(self.plugins)} enrichment plugins: {[p.__class__.__name__ for p in self.plugins]}"
        )

    async def start(self):
        """Start the enricher process for general enrichment"""
        async with self.lock:
            if self.running:
                logger.warning("Enricher already running, skipping")
                return
            self.running = True

        try:
            logger.info("Starting enrichment process")
            await self.run_enrichment()
            logger.info("Enricher process completed")
        except Exception as e:
            logger.exception(f"Error running enricher: {e}")
        finally:
            async with self.lock:
                self.running = False

    async def run_enrichment(self):
        """Run the enrichment process"""
        while True:
            # Process artists
            logger.info("Processing artists for enrichment")
            artist_update_count = await self._process_artists()

            # Process albums
            logger.info("Processing albums for enrichment")
            album_update_count = await self._process_albums()

            # Process tracks
            logger.info("Processing tracks for enrichment")
            track_update_count = await self._process_tracks()

            total_updates = (
                artist_update_count + album_update_count + track_update_count
            )

            if total_updates == 0:
                logger.info("No more items to process, finishing enrichment")
                break

    async def _process_artists(self) -> int:
        """Process non-enriched artists"""

        processed_artists = set()
        while True:
            logger.debug("Querying database for non-enriched artists...")
            artists = await self.db_manager.get_non_enriched_artists(limit=1)
            logger.debug(f"Found {len(artists)} non-enriched artists")
            if not artists:
                logger.info("No more artists to process")
                return len(processed_artists)
            artist = artists[0]
            logger.debug(f"Processing artist: {artist}")
            await self._enrich_artist(artist)
            processed_artists.add(artist["id"])
            logger.info(f"Processed artist {artist['name']}")

    async def _enrich_artist(self, artist):
        """Enrich a single artist"""
        logger.debug(
            f"Starting enrichment for artist {artist.get('name', artist['id'])}"
        )
        updated_artist = artist.copy()  # Make a copy to carry updates between plugins
        had_updates = False

        # Check if all required fields already exist and have values
        is_fully_enriched = all(
            updated_artist.get(field) for field in ARTIST_REQUIRED_FIELDS
        )
        if is_fully_enriched:
            logger.debug(f"Artist {artist['id']} already has all required fields")
            updated_artist["enriched"] = EnrichmentStatus.ENRICHED
            await self.db_manager.update_artist(
                artist["id"], {"enriched": EnrichmentStatus.ENRICHED}
            )
            return

        logger.debug(
            f"Artist {artist.get('name', artist['id'])} missing fields - starting plugin enrichment"
        )
        for plugin in self.plugins:
            if not plugin.can_enrich_artist():
                continue

            logger.info(
                f"Enriching artist {artist['name'] if 'name' in artist else artist['id']} with {plugin.__class__.__name__}"
            )
            result = await plugin.enrich_artist(updated_artist)
            if result and "updates" in result:
                logger.info(
                    f"Result from {plugin.__class__.__name__}: {result['updates'].keys()}"
                )
                # Apply updates to our working copy
                updated_artist.update(result["updates"])
                # Track that we had updates
                had_updates = True

                # Check if we're now fully enriched after this plugin
                is_fully_enriched = all(
                    updated_artist.get(field) for field in ARTIST_REQUIRED_FIELDS
                )
                if is_fully_enriched:
                    logger.debug(f"Artist {artist['name']} now fully enriched")
                    updated_artist["enriched"] = EnrichmentStatus.ENRICHED
                    break  # No need to check further plugins

        # After all plugins, if still not fully enriched, mark as failed
        if (
            not is_fully_enriched
            and updated_artist.get("enriched") != EnrichmentStatus.ENRICHED
        ):
            logger.debug(
                f"Artist {artist['id']} failed enrichment - missing required fields"
            )
            updated_artist["enriched"] = EnrichmentStatus.FAILED
            had_updates = True

        # Only update the database once at the end if we had any updates
        if had_updates:
            await self.db_manager.update_artist(artist["id"], updated_artist)

    async def _process_albums(self) -> int:
        """Process non-enriched albums"""
        processed_albums = set()
        while True:
            logger.debug("Querying database for non-enriched albums...")
            albums = await self.db_manager.get_non_enriched_albums(limit=1)
            logger.debug(f"Found {len(albums)} non-enriched albums")
            if not albums:
                logger.info("No more albums to process")
                return len(processed_albums)
            album = albums[0]
            logger.debug(f"Processing album: {album}")
            await self._enrich_album(album)
            processed_albums.add(album["id"])
            logger.info(f"Processed album {album['title']}")

    async def _enrich_album(self, album):
        """Enrich a single album"""
        updated_album = album.copy()  # Make a copy to carry updates between plugins
        had_updates = False

        # Check if all required fields already exist and have values
        is_fully_enriched = all(
            updated_album.get(field) for field in ALBUM_REQUIRED_FIELDS
        )
        if is_fully_enriched:
            logger.debug(f"Album {album['id']} already has all required fields")
            updated_album["enriched"] = EnrichmentStatus.ENRICHED
            await self.db_manager.update_album(
                album["id"], {"enriched": EnrichmentStatus.ENRICHED}
            )
            return

        for plugin in self.plugins:
            if not plugin.can_enrich_album():
                continue

            logger.info(
                f"Enriching album {album['name'] if 'name' in album else album['id']} with {plugin.__class__.__name__}"
            )
            result = await plugin.enrich_album(updated_album)
            if result and "updates" in result:
                logger.info(
                    f"Result from {plugin.__class__.__name__}: {result['updates'].keys()}"
                )
                # Apply updates to our working copy
                updated_album.update(result["updates"])
                # Track that we had updates
                had_updates = True

                # Check if we're now fully enriched after this plugin
                is_fully_enriched = all(
                    updated_album.get(field) for field in ALBUM_REQUIRED_FIELDS
                )
                if is_fully_enriched:
                    logger.debug(f"Album {album['title']} now fully enriched")
                    updated_album["enriched"] = EnrichmentStatus.ENRICHED
                    break

        # After all plugins, if still not fully enriched, mark as failed
        if (
            not is_fully_enriched
            and updated_album.get("enriched") != EnrichmentStatus.ENRICHED
        ):
            logger.debug(
                f"Album {album['id']} failed enrichment - missing required fields"
            )
            updated_album["enriched"] = EnrichmentStatus.FAILED
            had_updates = True

        # Only update the database once at the end if we had any updates
        if had_updates:
            await self.db_manager.update_album(album["id"], updated_album)

    async def _process_tracks(self) -> int:
        """Process non-enriched tracks"""
        processed_tracks = set()
        while True:
            logger.debug("Querying database for non-enriched tracks...")
            tracks = await self.db_manager.get_non_enriched_tracks(limit=1)
            logger.debug(f"Found {len(tracks)} non-enriched tracks")
            if not tracks:
                logger.info("No more tracks to process")
                return len(processed_tracks)
            track = tracks[0]
            logger.debug(f"Processing track: {track}")
            await self._enrich_track(track)
            processed_tracks.add(track["id"])
            logger.info(f"Processed track {track['title']}")

    async def _enrich_track(self, track):
        """Enrich a single track"""
        updated_track = track.copy()  # Make a copy to carry updates between plugins
        had_updates = False

        # Check if all required fields already exist and have values
        is_fully_enriched = all(
            updated_track.get(field) for field in TRACK_REQUIRED_FIELDS
        )
        if is_fully_enriched:
            logger.debug(f"Track {track['id']} already has all required fields")
            updated_track["enriched"] = EnrichmentStatus.ENRICHED
            await self.db_manager.update_track(
                track["id"], {"enriched": EnrichmentStatus.ENRICHED}
            )
            return

        # Collect any entities that need further enrichment
        entities_for_enrichment = {"artists": set(), "albums": set(), "tracks": set()}

        for plugin in self.plugins:
            if not plugin.can_enrich_track():
                continue

            # Skip remaining plugins if we become fully enriched
            if updated_track.get("enriched") == EnrichmentStatus.ENRICHED:
                logger.debug(
                    f"Track {track['id']} fully enriched, skipping remaining plugins"
                )
                break

            logger.info(
                f"Enriching track {track['title'] if 'title' in track else track['id']} with {plugin.__class__.__name__}"
            )
            result = await plugin.enrich_track(updated_track)
            if result and "updates" in result:
                logger.info(
                    f"Result from {plugin.__class__.__name__}: {result['updates'].keys()}"
                )
                # Apply updates to our working copy
                updated_track.update(result["updates"])
                # Track that we had updates
                had_updates = True

                # Check if we're now fully enriched after this plugin
                is_fully_enriched = all(
                    updated_track.get(field) for field in TRACK_REQUIRED_FIELDS
                )
                if is_fully_enriched:
                    logger.debug(f"Track {track['title']} now fully enriched")
                    updated_track["enriched"] = EnrichmentStatus.ENRICHED

                # # Check if plugin identified entities that need further enrichment
                # if "changed_items" in result:
                #     for entity_type in ["artists", "albums", "tracks"]:
                #         if entity_type in result["changed_items"]:
                #             entities_for_enrichment[entity_type].update(
                #                 result["changed_items"][entity_type]
                # )

        # After all plugins, if still not fully enriched, mark as failed
        if (
            not is_fully_enriched
            and updated_track.get("enriched") != EnrichmentStatus.ENRICHED
            and track["id"] not in entities_for_enrichment["tracks"]
        ):
            logger.info(
                f"Track {track['id']} failed enrichment - missing required fields"
            )
            updated_track["enriched"] = EnrichmentStatus.FAILED
            had_updates = True

        # Only update the database once at the end if we had any updates
        if had_updates:
            await self.db_manager.update_track(track["id"], updated_track)
            if "album_id" in updated_track:
                await self.db_manager.update_album_stats(updated_track["album_id"])

        # If we have entities that need further enrichment, add them to the enricher queue
        # if any(entities_for_enrichment.values()):
        #     changed_items = {
        #         "artists": list(entities_for_enrichment["artists"]),
        #         "albums": list(entities_for_enrichment["albums"]),
        #         "tracks": list(entities_for_enrichment["tracks"]),
        #     }

        #     logger.info(
        #         f"Queueing additional enrichment for entities from track {track['id']}: "
        #         f"Artists={len(changed_items['artists'])}, "
        #         f"Albums={len(changed_items['albums'])}, "
        #         f"Tracks={len(changed_items['tracks'])}"
        #     )

        #     # Add to the enricher queue
        #     global _enricher_queue
        #     await _enricher_queue.put({"changed_items": changed_items})


async def _enricher_worker(config, db_manager: AsyncEnricherDb):
    """Background worker task for the enricher"""

    enricher_instance = MetadataEnricher(config, db_manager)
    enricher_tasks = set()

    # Process queue commands
    while True:
        try:
            # Check for commands with timeout
            try:
                # Use run_in_executor to call the blocking get() in a non-blocking way
                loop = asyncio.get_running_loop()
                command = await loop.run_in_executor(
                    None, lambda: _enricher_queue.get(block=True, timeout=30.0)
                )

                logger.info("Received command from queue: %s", command)

                if command == "stop":
                    logger.info("Stopping enricher task")
                    _shutdown_event.set()
                    break
                elif command == "enrich":
                    logger.info("Manual enrichment triggered")
                    await enricher_instance.start()

            except queue.Empty:
                # No command received in 30 seconds, run periodic enrichment check
                logger.debug(
                    "No commands received, checking for new items to enrich..."
                )
                continue
                # await enricher_instance.start()

            # Small delay to avoid busy waiting
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.info("Enricher worker cancelled.")
            break
        except Exception as e:
            logger.exception(f"Error in enricher worker: {str(e)}")
            await asyncio.sleep(5)  # Sleep longer on errors

    running_tasks = list(enricher_tasks)
    for task in running_tasks:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def start_enricher(config, db_manager: AsyncEnricherDb) -> Optional[asyncio.Task]:
    """Start the enricher process in a background task"""
    global _enricher_task

    def log_task_result(task):
        try:
            task.result()  # Will raise if task failed
        except Exception as e:
            logger.exception(f"Enricher error: {e}")

    if _enricher_task and not _enricher_task.done():
        logger.warning("Enricher task already running, not starting another")
        return _enricher_task

    _enricher_task = asyncio.create_task(_enricher_worker(config, db_manager))
    _enricher_task.add_done_callback(log_task_result)

    logger.info("Started metadata enricher background task")
    return _enricher_task


async def stop_enricher() -> bool:
    """Stop the enricher task"""
    global _enricher_task
    if _enricher_task and not _enricher_task.done():
        logger.info("Sending stop command to enricher task")
        try:
            _enricher_queue.put("stop")

            await asyncio.wait_for(_enricher_task, timeout=10.0)
            return True
        except asyncio.TimeoutError:
            logger.warning("Enricher task did not stop in time, cancelling...")
            _enricher_task.cancel()
            try:
                await asyncio.wait_for(_enricher_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            return True
        except Exception as e:
            logger.exception(f"Error stopping enricher task: {str(e)}")
            return False
    elif _enricher_task and _enricher_task.done():
        logger.info("Enricher task was already done.")
        return True
    logger.info("No active enricher task to stop.")
    return False


async def async_main(config: LocalFilesConfig):
    """Runs the main application logic asynchronously."""
    global _enricher_task, _shutdown_event

    db_manager = AsyncEnricherDb(config)
    try:
        await db_manager.init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.exception(f"Fatal: Error initializing database: {str(e)}")
        sys.exit(1)

    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown_event.set)

    _enricher_task = start_enricher(config, db_manager)

    try:
        await _shutdown_event.wait()
    except asyncio.CancelledError:
        logger.info("Server cancelled, shutting down...")
    except Exception as e:
        logger.exception(f"Error in main loop: {str(e)}")
    finally:
        logger.info("Main async runner initiating shutdown of tasks...")

        if _enricher_task and not _enricher_task.done():
            await stop_enricher()
        else:
            logger.info("Enricher task already finished.")

        logger.info("All background tasks processed for shutdown.")


def main(
    config: LocalFilesConfig,
    enricher_queue: multiprocessing.Queue,
    logger_queue: multiprocessing.Queue,
):
    """Main entry point for the enricher daemon."""

    global _enricher_queue

    _enricher_queue = enricher_queue
    try:
        import logging.handlers

        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        root.addHandler(logging.handlers.QueueHandler(logger_queue))

        asyncio.run(async_main(config))
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received by asyncio.run. Exiting.")
    except Exception as e:
        logger.critical(f"Unhandled exception in asyncio.run: {e}", exc_info=True)
    finally:
        logger.info("Enricher daemon finished.")
