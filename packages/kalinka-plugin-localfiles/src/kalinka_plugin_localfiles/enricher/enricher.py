#!/usr/bin/env python3
import multiprocessing
import asyncio
import enum
import importlib.util
import json
import logging
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

# Global variables to manage enricher state. The enricher runs inside the
# librarian process (Phase 2b); the indexer feeds it "enrich"/"stop" over an
# in-process asyncio queue, and the searcher nudge stays cross-process.
_enricher_task: Optional[asyncio.Task] = None
_enricher_queue: Optional[asyncio.Queue] = None
# Nudges the searcher (a separate process) to re-tag once enrichment finishes;
# the searcher in turn nudges the embedder. Stays a cross-process queue.
_searcher_nudge_queue: Optional[multiprocessing.Queue] = None
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

        # Generated album art is strictly last: it only fills covers no
        # real source could provide, so every fetching plugin above must
        # get its chance first.
        if config.enricher.plugins.procedural_artwork.enabled:
            if importlib.util.find_spec("numpy") is not None:
                logger.info("Loading ProceduralArtworkPlugin")
                from .procedural_artwork_plugin import ProceduralArtworkPlugin

                self.plugins.append(ProceduralArtworkPlugin(config, self.db_manager))
            else:
                logger.warning(
                    "Generated album art is enabled but numpy is not "
                    "installed; skipping. Use 'Restart with install' on the "
                    "modules page to fetch it."
                )

        logger.info(
            f"Loaded {len(self.plugins)} enrichment plugins: {[p.__class__.__name__ for p in self.plugins]}"
        )

    def compute_fingerprint(self) -> str:
        """Stable signature of the *active* enrichment setup.

        Built from ``self.plugins`` in load order — so it captures which
        plugins are enabled and in what order (first match wins), each
        one's ``ENRICHER_VERSION`` (bumped when its matching code
        changes), and its ``config_signature()`` (match-affecting config
        such as MB thresholds or whether an AcoustID key is set).

        Only enabled plugins contribute, which is the whole point:
        bumping the version of, or reconfiguring, a plugin the user has
        *disabled* leaves the fingerprint untouched and so triggers no
        needless retry sweep. The fingerprint changing is the signal
        that previously-FAILED rows deserve another attempt.
        """
        components = [
            {
                "name": p.__class__.__name__,
                "version": p.ENRICHER_VERSION,
                "config": p.config_signature(),
            }
            for p in self.plugins
        ]
        return json.dumps(components, sort_keys=True)

    async def start(self):
        """Start the enricher process for general enrichment"""
        async with self.lock:
            if self.running:
                logger.warning("Enricher already running, skipping")
                return
            self.running = True

        try:
            # Idle-path logs demoted to DEBUG: the indexer sends "enrich"
            # after every scheduled scan (default every 5 min), so on a
            # quiescent library the user used to see 11 INFO lines of
            # nothing-to-do noise per pass.
            logger.debug("Starting enrichment process")
            await self.run_enrichment()
            logger.debug("Enricher process completed")
        except Exception as e:
            logger.exception(f"Error running enricher: {e}")
        finally:
            async with self.lock:
                self.running = False

    async def run_enrichment(self):
        """Run the enrichment process"""
        totals = {"artists": 0, "albums": 0, "tracks": 0}
        while True:
            logger.debug("Processing artists for enrichment")
            artist_update_count = await self._process_artists()
            logger.debug("Processing albums for enrichment")
            album_update_count = await self._process_albums()
            logger.debug("Processing tracks for enrichment")
            track_update_count = await self._process_tracks()

            totals["artists"] += artist_update_count
            totals["albums"] += album_update_count
            totals["tracks"] += track_update_count

            total_updates = (
                artist_update_count + album_update_count + track_update_count
            )

            if total_updates == 0:
                logger.debug("No more items to process, finishing enrichment")
                break

        # Single INFO summary, only when the pass actually did work. On
        # an idle library this stays silent entirely.
        if any(totals.values()):
            logger.info(
                "Enrichment pass complete: %d artists, %d albums, %d tracks updated",
                totals["artists"],
                totals["albums"],
                totals["tracks"],
            )

    async def _process_artists(self) -> int:
        """Process non-enriched artists"""

        processed_artists = set()
        while True:
            logger.debug("Querying database for non-enriched artists...")
            artists = await self.db_manager.get_non_enriched_artists(limit=1)
            logger.debug(f"Found {len(artists)} non-enriched artists")
            if not artists:
                logger.debug("No more artists to process")
                return len(processed_artists)
            artist = artists[0]
            logger.debug(f"Processing artist: {artist}")
            await self._enrich_artist(artist)
            processed_artists.add(artist["id"])
            logger.debug(f"Processed artist {artist['name']}")

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

            logger.debug(
                f"Enriching artist {artist['name'] if 'name' in artist else artist['id']} with {plugin.__class__.__name__}"
            )
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

    async def _process_albums(self) -> int:
        """Process non-enriched albums"""
        processed_albums = set()
        while True:
            logger.debug("Querying database for non-enriched albums...")
            albums = await self.db_manager.get_non_enriched_albums(limit=1)
            logger.debug(f"Found {len(albums)} non-enriched albums")
            if not albums:
                logger.debug("No more albums to process")
                return len(processed_albums)
            album = albums[0]
            logger.debug(f"Processing album: {album}")
            await self._enrich_album(album)
            processed_albums.add(album["id"])
            logger.debug(f"Processed album {album['title']}")

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
                f"Enriching album {album['name'] if 'name' in album else album['id']} with {plugin.__class__.__name__}"
            )
            result = await plugin.enrich_album(updated_album)
            if result and "updates" in result:
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
                logger.debug("No more tracks to process")
                return len(processed_tracks)
            track = tracks[0]
            logger.debug(f"Processing track: {track}")
            await self._enrich_track(track)
            processed_tracks.add(track["id"])
            logger.debug(f"Processed track {track['title']}")

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

        for plugin in self.plugins:
            if not plugin.can_enrich_track():
                continue

            # Skip remaining plugins if we become fully enriched
            if updated_track.get("enriched") == EnrichmentStatus.ENRICHED:
                logger.debug(
                    f"Track {track['id']} fully enriched, skipping remaining plugins"
                )
                break

            logger.debug(
                f"Enriching track {track['title'] if 'title' in track else track['id']} with {plugin.__class__.__name__}"
            )
            result = await plugin.enrich_track(updated_track)
            if result and "updates" in result:
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

        # After all plugins, if still not fully enriched, mark as failed
        if (
            not is_fully_enriched
            and updated_track.get("enriched") != EnrichmentStatus.ENRICHED
        ):
            missing = [f for f in TRACK_REQUIRED_FIELDS if not updated_track.get(f)]
            # file_path identifies the track even when title/artist are missing
            where = updated_track.get("file_path") or track["id"]
            logger.debug(
                "Track %s failed enrichment - missing: %s",
                where,
                ", ".join(missing),
            )
            updated_track["enriched"] = EnrichmentStatus.FAILED
            had_updates = True

        # Only update the database once at the end if we had any updates
        if had_updates:
            await self.db_manager.update_track(track["id"], updated_track)
            if "album_id" in updated_track:
                await self.db_manager.update_album_stats(updated_track["album_id"])


async def _enricher_worker(config, db_manager: AsyncEnricherDb):
    """Background worker task for the enricher"""

    if _enricher_queue is None:
        logger.error("Enricher queue is not initialized; stopping worker")
        _shutdown_event.set()
        return

    enricher_instance = MetadataEnricher(config, db_manager)

    # One-time, *gated* retry of previously-FAILED rows. A plain restart
    # no longer re-hammers MusicBrainz/Deezer for rows that will fail
    # identically: FAILED rows are re-opened only when the enrichment
    # fingerprint changed since the last run — i.e. a plugin was
    # enabled/disabled/reordered, a plugin's ENRICHER_VERSION was bumped
    # (new matcher code), or a match-affecting config field changed (an
    # AcoustID key added, an MB threshold lowered). The first run after
    # this ships has no stored fingerprint, so it resets once and then
    # settles.
    try:
        fingerprint = enricher_instance.compute_fingerprint()
        retried = await db_manager.reset_failed_for_fingerprint(fingerprint)
        if retried is None:
            logger.info(
                "Enrichment fingerprint unchanged; leaving FAILED rows as-is"
            )
        else:
            logger.info(
                "Enrichment setup changed; reset %d previously-FAILED rows for "
                "retry: %d artists, %d albums, %d tracks",
                sum(retried.values()),
                retried["artists"],
                retried["albums"],
                retried["tracks"],
            )
    except Exception as e:
        logger.warning(f"Failed-row retry reset skipped: {e}")

    # Process queue commands
    while True:
        try:
            # Check for commands with timeout
            try:
                command = await asyncio.wait_for(
                    _enricher_queue.get(), timeout=30.0
                )

                logger.debug("Received command from queue: %s", command)

                if command == "stop":
                    logger.info("Stopping enricher task")
                    _shutdown_event.set()
                    break
                elif command == "enrich":
                    logger.debug("Manual enrichment triggered")
                    await enricher_instance.start()
                    if _searcher_nudge_queue is not None:
                        try:
                            _searcher_nudge_queue.put_nowait("nudge")
                        except Exception:
                            pass

            except asyncio.TimeoutError:
                # No command in the timeout window; loop and wait again.
                logger.debug("No enricher commands received; continuing to wait")
                continue

            # Small delay to avoid busy waiting
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.info("Enricher worker cancelled.")
            break
        except Exception as e:
            logger.exception(f"Error in enricher worker: {str(e)}")
            await asyncio.sleep(5)  # Sleep longer on errors


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
            if _enricher_queue is None:
                logger.error("Enricher queue is not initialized; cannot send stop")
                return False
            _enricher_queue.put_nowait("stop")

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


# Process orchestration lives in librarian.py, which runs this enricher's
# worker loop and the indexer's in one event loop (Phase 2b). start_enricher /
# stop_enricher above are its API.
