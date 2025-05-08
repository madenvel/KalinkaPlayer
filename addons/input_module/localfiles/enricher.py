import logging
import time
import threading
import queue

from .enricher_plugin import EnricherPlugin
from .musicbrainz_plugin import MusicBrainzPlugin
from .acoustid_plugin import AcoustIdPlugin
from .wikidata_plugin import WikidataPlugin
from .deezer_plugin import DeezerPlugin

logger = logging.getLogger(__name__.split(".")[-1])

# Set MusicBrainzNGS log level to warning to reduce verbosity
musicbrainz_logger = logging.getLogger("musicbrainzngs")
musicbrainz_logger.setLevel(logging.WARNING)

# Global variables to manage enricher state
_enricher_thread = None
_enricher_instance = None
_enricher_queue = queue.Queue()


class MetadataEnricher:
    """Main enricher class that processes database entries"""

    def __init__(self, config, db_manager):
        self.config = config
        self.db_manager = db_manager
        self.running = False
        self.lock = threading.Lock()
        self.plugins = []

        # The order of plugins matters for the enrichment process
        # AcoustID should be first to make sure it finds the metadata
        # for the files missing tags before we can get additional
        #  metadata from MusicBrainz
        if config["enricher.plugins.acoustid.enabled"]:
            self.plugins.append(AcoustIdPlugin(config, db_manager))

        if config["enricher.plugins.musicbrainz.enabled"]:
            self.plugins.append(MusicBrainzPlugin(config, db_manager))

        if config["enricher.plugins.wikidata.enabled"]:
            self.plugins.append(WikidataPlugin(config, db_manager))

        if config["enricher.plugins.deezer.enabled"]:
            self.plugins.append(DeezerPlugin(config, db_manager))

    def process_changed_items(self, changed_items):
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
                artist = self.db_manager.get_artist_by_id(artist_id)
                if artist:
                    self._enrich_artist(artist)

        # Process specific albums
        for album_id in changed_items.get("albums", []):
            if album_id and album_id != "unknown_album":
                album = self.db_manager.get_album_by_id(album_id)
                if album:
                    self._enrich_album(album)

        # Process specific tracks
        for track_id in changed_items.get("tracks", []):
            if track_id:
                track = self.db_manager.get_track_by_id(track_id)
                if track:
                    self._enrich_track(track)

    def start(self):
        """Start the enricher process for general enrichment"""
        if self.running:
            logger.warning("Enricher already running, skipping")
            return

        with self.lock:
            self.running = True
            try:
                self.run_enrichment()
                logger.info("Enricher process completed")
            except Exception as e:
                logger.error(f"Error running enricher: {str(e)}")
            finally:
                self.running = False

    def run_enrichment(self):
        """Run the enrichment process"""
        # Process artists
        logger.info("Processing artists for enrichment")
        self._process_artists()

        # Process albums
        logger.info("Processing albums for enrichment")
        self._process_albums()

        # Process tracks
        logger.info("Processing tracks for enrichment")
        self._process_tracks()

    def _process_artists(self, batch_size=10):
        """Process non-enriched artists"""
        artists = self.db_manager.get_non_enriched_artists(batch_size)

        for artist in artists:
            self._enrich_artist(artist)

    def _enrich_artist(self, artist):
        """Enrich a single artist"""
        updated_artist = artist.copy()  # Make a copy to carry updates between plugins
        had_updates = False

        for plugin in self.plugins:
            if not plugin.can_enrich_artist():
                continue

            result = plugin.enrich_artist(updated_artist)
            if result and "updates" in result:
                # Apply updates to our working copy
                updated_artist.update(result["updates"])
                # Track that we have updates
                had_updates = True

        # Only update the database once at the end if we had any updates
        if had_updates:
            self.db_manager.update_artist(artist["id"], updated_artist)

    def _process_albums(self, batch_size=500):
        """Process non-enriched albums"""
        albums = self.db_manager.get_non_enriched_albums(batch_size)

        for album in albums:
            self._enrich_album(album)

    def _enrich_album(self, album):
        """Enrich a single album"""
        updated_album = album.copy()  # Make a copy to carry updates between plugins
        had_updates = False

        for plugin in self.plugins:
            if not plugin.can_enrich_album():
                continue
            logger.debug(
                f"Enriching album {album['id']} with {plugin.__class__.__name__}"
            )
            result = plugin.enrich_album(updated_album)
            logger.debug(f"Result from {plugin.__class__.__name__}: {result}")
            if result and "updates" in result:
                logger.debug(f"Updates found: {result['updates']}")
                # Apply updates to our working copy
                updated_album.update(result["updates"])
                # Track that we had updates
                had_updates = True

        # Only update the database once at the end if we had any updates
        if had_updates:
            self.db_manager.update_album(album["id"], updated_album)

    def _process_tracks(self, batch_size=500):
        """Process non-enriched tracks"""
        tracks = self.db_manager.get_non_enriched_tracks(batch_size)

        for track in tracks:
            self._enrich_track(track)

    def _enrich_track(self, track):
        """Enrich a single track"""
        updated_track = track.copy()  # Make a copy to carry updates between plugins
        had_updates = False

        # Collect any entities that need further enrichment
        entities_for_enrichment = {"artists": set(), "albums": set(), "tracks": set()}

        for plugin in self.plugins:
            if not plugin.can_enrich_track():
                continue

            result = plugin.enrich_track(updated_track)
            if result:
                if "updates" in result:
                    # Apply updates to our working copy
                    updated_track.update(result["updates"])
                    # Track that we had updates
                    had_updates = True

                # Check if plugin identified entities that need further enrichment
                if "changed_items" in result:
                    for entity_type in ["artists", "albums", "tracks"]:
                        if entity_type in result["changed_items"]:
                            entities_for_enrichment[entity_type].update(
                                result["changed_items"][entity_type]
                            )

        # Only update the database once at the end if we had any updates
        if had_updates:
            self.db_manager.update_track(track["id"], updated_track)

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
            _enricher_queue.put({"changed_items": changed_items})


def _enricher_worker(config, db_manager):
    """Background worker thread for the enricher"""
    global _enricher_instance

    _enricher_instance = MetadataEnricher(config, db_manager)

    logger.info("Metadata enricher thread running")
    logger.info("Waiting for indexer to complete initial scan")

    initial_scan_complete = False
    # Process queue commands
    while True:
        try:
            # Check for commands
            try:
                command = _enricher_queue.get(timeout=60)  # Check every minute

                if initial_scan_complete is False:
                    logger.info("Starting enricher after initial scan")
                    initial_scan_complete = True
                    # Start the enricher process
                    _enricher_instance.start()
                    logger.info(
                        "Initial enrichment finished, listening for indexer updates"
                    )
                    command = None

                if command == "stop":
                    logger.info("Stopping enricher thread")
                    break
                elif command == "enrich":
                    logger.info("Manual enrichment triggered")
                    _enricher_instance.start()
                elif isinstance(command, dict) and "changed_items" in command:
                    # This is a notification from the indexer
                    logger.info("Processing changed items from indexer")
                    _enricher_instance.process_changed_items(command["changed_items"])

                _enricher_queue.task_done()
            except queue.Empty:
                # No command received, just continue
                pass

            # Sleep to avoid busy-waiting
            time.sleep(1)
        except Exception as e:
            logger.error(f"Error in enricher worker: {str(e)}")
            time.sleep(5)  # Sleep longer on errors


def indexer_callback(event):
    """Callback function to be registered with the indexer"""
    if _enricher_thread and _enricher_thread.is_alive():
        logger.info("Received change notification from indexer")
        _enricher_queue.put(event)
        return True
    else:
        logger.warning("Cannot process indexer changes - enricher thread not running")
        return False


def trigger_enrichment():
    """Manually trigger enrichment (can be called from other modules)"""
    if _enricher_thread and _enricher_thread.is_alive():
        logger.info("Triggering manual enrichment")
        _enricher_queue.put("enrich")
        return True
    else:
        logger.warning("Cannot trigger enrichment - enricher thread not running")
        return False


def start_enricher(config, db_manager):
    """Start the enricher process in a background thread"""
    global _enricher_thread

    # Ensure we don't start multiple enricher threads
    if _enricher_thread and _enricher_thread.is_alive():
        logger.warning("Enricher thread already running, not starting another")
        return _enricher_thread

    # Import indexer and register our callback
    from addons.input_module.localfiles.indexer import register_enricher_callback

    # Start the worker thread
    _enricher_thread = threading.Thread(
        target=_enricher_worker, args=(config, db_manager), daemon=True
    )
    _enricher_thread.name = "LocalFiles-Enricher"
    _enricher_thread.start()

    # Register callback with indexer
    register_enricher_callback(indexer_callback)

    logger.info("Started metadata enricher background thread")
    return _enricher_thread


def stop_enricher():
    """Stop the enricher thread"""
    if _enricher_thread and _enricher_thread.is_alive():
        logger.info("Sending stop command to enricher thread")
        _enricher_queue.put("stop")
        _enricher_thread.join(5)
        return True
    return False
