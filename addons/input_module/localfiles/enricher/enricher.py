#!/usr/bin/env python3
import os
import argparse
import asyncio
import enum
import json
import logging
import socket
import sys
import tempfile
from typing import Dict, Optional, Any

from musicbrainz_plugin import MusicBrainzPlugin
from acoustid_plugin import AcoustIdPlugin
from wikidata_plugin import WikidataPlugin
from deezer_plugin import DeezerPlugin
from enricher_db import AsyncEnricherDb

logger = logging.getLogger("enricher")

# Set MusicBrainzNGS log level to warning to reduce verbosity
musicbrainz_logger = logging.getLogger("musicbrainzngs")
musicbrainz_logger.setLevel(logging.WARNING)

# Required fields for each entity type
# These define what metadata fields are required for an entity to be considered fully enriched
ARTIST_REQUIRED_FIELDS = ["name", "mbid", "image_url"]
ALBUM_REQUIRED_FIELDS = ["title", "artist_id", "mbid", "cover_art", "year", "genre"]
TRACK_REQUIRED_FIELDS = [
    "title",
    "artist_id",
    "album_id",
    "mbid",
    "duration",
    "track_number",
]

# Socket path for IPC
SOCKET_PATH = os.path.join(tempfile.gettempdir(), "kalinka-enricher.sock")

# Global variables to manage enricher state
_server = None
_enricher_task: Optional[asyncio.Task] = None
_enricher_queue: asyncio.Queue = asyncio.Queue()


class EnrichmentStatus(enum.IntEnum):
    """Enum for enrichment status values"""

    NOT_ENRICHED = 0  # Initial state, needs enrichment
    ENRICHED = 1  # Successfully enriched with all required fields
    FAILED = 2  # Failed enrichment, missing required fields after all plugins


class MetadataEnricher:
    """Main enricher class that processes database entries using async"""

    def __init__(self, config, db_manager: AsyncEnricherDb):
        self.config = config
        self.db_manager = db_manager
        self.running = False
        self.lock = asyncio.Lock()
        self.plugins = []

        # The order of plugins matters for the enrichment process
        # as the first one found a match will be used
        if config.get("enricher.plugins.acoustid.enabled", False):
            self.plugins.append(AcoustIdPlugin(config, self.db_manager))

        if config.get("enricher.plugins.musicbrainz.enabled", False):
            self.plugins.append(MusicBrainzPlugin(config, self.db_manager))

        if config.get("enricher.plugins.wikidata.enabled", False):
            self.plugins.append(WikidataPlugin(config, self.db_manager))

        if config.get("enricher.plugins.deezer.enabled", False):
            self.plugins.append(DeezerPlugin(config, self.db_manager))

    async def process_changed_items(self, changed_items):
        """Process specific items that were changed by the indexer"""
        if not changed_items:
            return

        logger.info(
            f"Processing changed items: Artists={len(changed_items.get('artists', []))}, "
            f"Albums={len(changed_items.get('albums', []))}, Tracks={len(changed_items.get('tracks', []))}"
        )

        # Process specific artists
        for artist_id in changed_items.get("artists", []):
            if artist_id and artist_id != "unknown_artist":
                artist = await self.db_manager.get_artist_by_id(artist_id)
                if artist:
                    await self._enrich_artist(artist)

        # Process specific albums
        for album_id in changed_items.get("albums", []):
            if album_id and album_id != "unknown_album":
                album = await self.db_manager.get_album_by_id(album_id)
                if album:
                    await self._enrich_album(album)

        # Process specific tracks
        for track_id in changed_items.get("tracks", []):
            if track_id:
                track = await self.db_manager.get_track_by_id(track_id)
                if track:
                    await self._enrich_track(track)

    async def start(self):
        """Start the enricher process for general enrichment"""
        async with self.lock:
            if self.running:
                logger.warning("Enricher already running, skipping")
                return
            self.running = True

        try:
            await self.run_enrichment()
            logger.info("Enricher process completed")
        except Exception as e:
            logger.exception(f"Error running enricher: {e}")
        finally:
            async with self.lock:
                self.running = False

    async def run_enrichment(self):
        """Run the enrichment process"""
        # Process artists
        logger.info("Processing artists for enrichment")
        await self._process_artists()

        # Process albums
        logger.info("Processing albums for enrichment")
        await self._process_albums()

        # Process tracks
        logger.info("Processing tracks for enrichment")
        await self._process_tracks()

    async def _process_artists(self, batch_size=10):
        """Process non-enriched artists"""
        artists = await self.db_manager.get_non_enriched_artists(batch_size)

        for artist in artists:
            await self._enrich_artist(artist)

    async def _enrich_artist(self, artist):
        """Enrich a single artist"""
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

        for plugin in self.plugins:
            if not plugin.can_enrich_artist():
                continue

            result = await plugin.enrich_artist(updated_artist)
            if result and "updates" in result:
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

    async def _process_albums(self, batch_size=500):
        """Process non-enriched albums"""
        albums = await self.db_manager.get_non_enriched_albums(batch_size)

        for album in albums:
            await self._enrich_album(album)

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

            logger.debug(
                f"Enriching album {album['id']} with {plugin.__class__.__name__}"
            )
            result = await plugin.enrich_album(updated_album)
            logger.debug(f"Result from {plugin.__class__.__name__}: {result}")
            if result and "updates" in result:
                logger.debug(f"Updates found: {result['updates']}")
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

    async def _process_tracks(self, batch_size=500):
        """Process non-enriched tracks"""
        tracks = await self.db_manager.get_non_enriched_tracks(batch_size)

        for track in tracks:
            await self._enrich_track(track)

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

            result = await plugin.enrich_track(updated_track)
            if result:
                if "updates" in result:
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

                # Check if plugin identified entities that need further enrichment
                if "changed_items" in result:
                    for entity_type in ["artists", "albums", "tracks"]:
                        if entity_type in result["changed_items"]:
                            entities_for_enrichment[entity_type].update(
                                result["changed_items"][entity_type]
                            )

        # After all plugins, if still not fully enriched, mark as failed
        if (
            not is_fully_enriched
            and updated_track.get("enriched") != EnrichmentStatus.ENRICHED
            and track["id"] not in entities_for_enrichment["tracks"]
        ):
            logger.debug(
                f"Track {track['id']} failed enrichment - missing required fields"
            )
            updated_track["enriched"] = EnrichmentStatus.FAILED
            had_updates = True

        # Only update the database once at the end if we had any updates
        if had_updates:
            logger.info(
                f"Updating track {track['name']} with new metadata: {updated_track.keys()}"
            )
            await self.db_manager.update_track(track["id"], updated_track)

        # If we have entities that need further enrichment, add them to the enricher queue
        if any(entities_for_enrichment.values()):
            changed_items = {
                "artists": list(entities_for_enrichment["artists"]),
                "albums": list(entities_for_enrichment["albums"]),
                "tracks": list(entities_for_enrichment["tracks"]),
            }

            logger.info(
                f"Queueing additional enrichment for entities from track {track['id']}: "
                f"Artists={len(changed_items['artists'])}, "
                f"Albums={len(changed_items['albums'])}, "
                f"Tracks={len(changed_items['tracks'])}"
            )

            # Add to the enricher queue
            global _enricher_queue
            await _enricher_queue.put({"changed_items": changed_items})


async def _enricher_worker(config, db_manager: AsyncEnricherDb):
    """Background worker task for the enricher"""

    enricher_instance = MetadataEnricher(config, db_manager)
    enricher_tasks = set()

    def run_enricher_task(coro):
        """Run the enricher task"""
        task = asyncio.create_task(coro)
        enricher_tasks.add(task)
        task.add_done_callback(enricher_tasks.discard)
        return task

    logger.info("Starting initial enrichment process")
    run_enricher_task(enricher_instance.start()).add_done_callback(
        lambda task: logger.info("Initial enrichment process completed")
    )

    # Process queue commands
    while True:
        try:
            # Check for commands
            try:
                command = await _enricher_queue.get()

                if command == "stop":
                    logger.info("Stopping enricher task")
                    break
                elif command == "enrich":
                    logger.info("Manual enrichment triggered")
                    run_enricher_task(enricher_instance.start())
                elif isinstance(command, dict) and "changed_items" in command:
                    # This is a notification from the indexer
                    run_enricher_task(
                        enricher_instance.process_changed_items(
                            command["changed_items"]
                        )
                    )

                _enricher_queue.task_done()
            except asyncio.TimeoutError:
                # No command received, just continue
                pass

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


async def handle_client(reader, writer):
    """Handle Unix socket client connection"""
    data = await reader.readline()
    message = data.decode().strip()

    try:
        # Try to parse as JSON
        command = json.loads(message)
        await _enricher_queue.put(command)
    except json.JSONDecodeError:
        # Simple string command
        await _enricher_queue.put(message)

    writer.close()
    await writer.wait_closed()


def ensure_single_instance():
    """Try to connect to the existing socket to see if it's already running."""
    if os.path.exists(SOCKET_PATH):
        try:
            # Try to connect to see if the socket is active
            test_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            test_socket.connect(SOCKET_PATH)
            test_socket.close()
            print("Another instance is already running.")
            sys.exit(1)
        except (ConnectionRefusedError, FileNotFoundError):
            print("Stale socket found. Cleaning up.")
            os.remove(SOCKET_PATH)  # stale socket


async def start_server():
    """Start the Unix socket server process"""
    global _server

    if _server and not _server.is_serving():
        logger.warning("Server already running, not starting another")
        return _server

    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    _server = await asyncio.start_unix_server(handle_client, path=SOCKET_PATH)
    logger.info(f"Server started at {SOCKET_PATH}")
    return _server


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
            await _enricher_queue.put("stop")
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


async def main(config_data: Dict[str, Any]):
    """Runs the main application logic asynchronously."""
    global _enricher_task

    ensure_single_instance()

    db_manager = AsyncEnricherDb(config_data)
    try:
        await db_manager.init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.exception(f"Fatal: Error initializing database: {str(e)}")
        sys.exit(1)

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)

    _enricher_task = start_enricher(config_data, db_manager)
    _server = await start_server()

    try:
        await shutdown_event.wait()
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

        if _server:
            _server.close()
            await _server.wait_closed()

        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)

        logger.info("Server shutdown complete.")


if __name__ == "__main__":
    import signal

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="Async Metadata enricher daemon")
    parser.add_argument(
        "-c", "--config", help="JSON configuration string", required=True
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set log level",
    )
    parser.add_argument(
        "--show-default-config",
        action="store_true",
        help="Print default configuration template and exit",
    )
    args = parser.parse_args()

    # Configure logging level
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # Show default config if requested
    if args.show_default_config:
        default_config = {
            "enricher.plugins.acoustid.enabled": True,
            "enricher.plugins.acoustid.api_key": "YOUR_ACOUSTID_API_KEY",
            "enricher.plugins.musicbrainz.enabled": True,
            "enricher.plugins.musicbrainz.app_name": "YourAppName",
            "enricher.plugins.musicbrainz.app_version": "1.0",
            "enricher.plugins.musicbrainz.contact_info": "your@email.com",
            "enricher.plugins.wikidata.enabled": True,
            "enricher.plugins.deezer.enabled": True,
            "db_path": "/path/to/your/database.sqlite",
        }
        print(json.dumps(default_config, indent=2))
        sys.exit(0)

    logger.info("Loading configuration from command line JSON")
    try:
        loaded_config = json.loads(args.config)
        if (
            "input_modules" in loaded_config
            and "localfiles" in loaded_config["input_modules"]
        ):
            app_config = loaded_config["input_modules"]["localfiles"]
            logger.info(f"Loaded config: {app_config}")
        else:
            app_config = loaded_config
            logger.info("Loaded direct config")
    except json.JSONDecodeError as e:
        logger.exception(f"Error parsing JSON configuration: {str(e)}")
        sys.exit(1)
    except KeyError as e:
        logger.exception(f"Configuration missing expected keys: {str(e)}")
        sys.exit(1)

    try:
        asyncio.run(main(app_config))
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Exiting.")
    except Exception as e:
        logger.critical(f"Unhandled exception: {e}", exc_info=True)
    finally:
        logger.info("Enricher daemon finished.")
