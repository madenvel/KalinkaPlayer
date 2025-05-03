import logging
import time
import threading
import os
import abc
import requests
from typing import Dict, List, Optional, Any, Set
from PIL import Image
import io
import musicbrainzngs
import queue

logger = logging.getLogger(__name__.split(".")[-1])

# Set MusicBrainzNGS log level to warning to reduce verbosity
musicbrainz_logger = logging.getLogger("musicbrainzngs")
musicbrainz_logger.setLevel(logging.WARNING)

# Global variables to manage enricher state
_enricher_thread = None
_enricher_instance = None
_enricher_queue = queue.Queue()


class EnricherPlugin(abc.ABC):
    """Base class for enricher plugins"""

    @abc.abstractmethod
    def can_enrich_artist(self) -> bool:
        """Whether this plugin can enrich artist metadata"""
        pass

    @abc.abstractmethod
    def can_enrich_album(self) -> bool:
        """Whether this plugin can enrich album metadata"""
        pass

    @abc.abstractmethod
    def can_enrich_track(self) -> bool:
        """Whether this plugin can enrich track metadata"""
        pass

    @abc.abstractmethod
    def enrich_artist(self, artist: Dict) -> Optional[Dict]:
        """Enrich artist metadata"""
        pass

    @abc.abstractmethod
    def enrich_album(self, album: Dict) -> Optional[Dict]:
        """Enrich album metadata"""
        pass

    @abc.abstractmethod
    def enrich_track(self, track: Dict) -> Optional[Dict]:
        """Enrich track metadata"""
        pass


class MusicBrainzPlugin(EnricherPlugin):
    """MusicBrainz metadata enrichment plugin"""

    def __init__(self, config, db_manager):
        self.config = config
        self.db_manager = db_manager
        self.threshold = config["enricher.plugins.musicbrainz.match_threshold"]
        self.user_agent = config["enricher.plugins.musicbrainz.user_agent"]

        # Set up MusicBrainz API
        musicbrainzngs.set_useragent(
            self.user_agent.split("/")[0],
            self.user_agent.split("/")[1].split(" ")[0],
            self.user_agent.split(" ", 1)[1].strip("()"),
        )

        # Rate limiting
        self.last_request_time = 0
        self.request_interval = 1 / 3  # 3 requests per second

    def _wait_for_rate_limit(self):
        """Wait to respect rate limits"""
        now = time.time()
        elapsed = now - self.last_request_time

        if elapsed < self.request_interval:
            sleep_time = self.request_interval - elapsed
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    def can_enrich_artist(self) -> bool:
        return True

    def can_enrich_album(self) -> bool:
        return True

    def can_enrich_track(self) -> bool:
        return True

    def enrich_artist(self, artist: Dict) -> Optional[Dict]:
        """Enrich artist metadata with MusicBrainz data"""
        try:
            if artist["id"] == "unknown_artist":
                return None

            logger.debug(f"Enriching artist: {artist['name']}")

            # Search for artist in MusicBrainz
            self._wait_for_rate_limit()
            result = musicbrainzngs.search_artists(artist["name"])

            if not result["artist-list"]:
                logger.debug(f"No MusicBrainz match found for artist: {artist['name']}")
                return None

            # Find best match
            best_match = None
            best_score = 0

            for mb_artist in result["artist-list"]:
                score = int(mb_artist.get("ext:score", 0))
                if score > best_score:
                    best_score = score
                    best_match = mb_artist

            if best_score < self.threshold:
                logger.debug(
                    f"No good MusicBrainz match for artist: {artist['name']} (best score: {best_score})"
                )
                return None

            # Get more details from artist
            artist_mbid = best_match["id"]
            self._wait_for_rate_limit()
            mb_artist_details = musicbrainzngs.get_artist_by_id(
                artist_mbid, includes=["url-rels"]
            )
            mb_artist_data = mb_artist_details["artist"]

            # Update artist data
            updates = {"mbid": artist_mbid, "enriched": 1}

            # Return data for further enrichment if needed
            return {"updates": updates, "mbid": artist_mbid}

        except Exception as e:
            logger.error(f"Error enriching artist {artist['name']}: {str(e)}")
            return None

    def enrich_album(self, album: Dict) -> Optional[Dict]:
        """Enrich album metadata with MusicBrainz data"""
        try:
            if album["id"] == "unknown_album":
                return None

            logger.debug(f"Enriching album: {album['title']} by {album['artist_name']}")

            # Search for album in MusicBrainz
            self._wait_for_rate_limit()
            result = musicbrainzngs.search_releases(
                album["title"], artistname=album["artist_name"]
            )

            if not result["release-list"]:
                logger.debug(
                    f"No MusicBrainz match found for album: {album['title']} by {album['artist_name']}"
                )
                return None

            # Find best match
            best_match = None
            best_score = 0

            for mb_release in result["release-list"]:
                score = int(mb_release.get("ext:score", 0))
                if score > best_score:
                    best_score = score
                    best_match = mb_release

            if best_score < self.threshold:
                logger.debug(
                    f"No good MusicBrainz match for album: {album['title']} (best score: {best_score})"
                )
                return None

            # Get more details about the release
            release_mbid = best_match["id"]
            self._wait_for_rate_limit()
            mb_release_details = musicbrainzngs.get_release_by_id(
                release_mbid, includes=["recordings", "artist-credits", "genres"]
            )
            mb_release_data = mb_release_details["release"]

            # Update album data
            updates = {"mbid": release_mbid, "enriched": 1}

            # Add genre if available
            if "genre-list" in mb_release_data and mb_release_data["genre-list"]:
                updates["genre"] = mb_release_data["genre-list"][0]["name"]

            # Add year if available
            if "date" in mb_release_data:
                try:
                    updates["year"] = int(mb_release_data["date"].split("-")[0])
                except (ValueError, IndexError):
                    pass

            # Return data for further enrichment if needed
            return {"updates": updates, "mbid": release_mbid}

        except Exception as e:
            logger.error(f"Error enriching album {album['title']}: {str(e)}")
            return None

    def enrich_track(self, track: Dict) -> Optional[Dict]:
        """Enrich track metadata with MusicBrainz data"""
        try:
            logger.debug(
                f"Enriching track: {track['title']} from {track['album_title']}"
            )

            # Search for recording in MusicBrainz
            self._wait_for_rate_limit()
            result = musicbrainzngs.search_recordings(
                track["title"],
                artistname=track["artist_name"],
                release=track["album_title"],
            )

            if not result["recording-list"]:
                logger.debug(f"No MusicBrainz match found for track: {track['title']}")
                return None

            # Find best match
            best_match = None
            best_score = 0

            for mb_recording in result["recording-list"]:
                score = int(mb_recording.get("ext:score", 0))
                if score > best_score:
                    best_score = score
                    best_match = mb_recording

            if best_score < self.threshold:
                logger.debug(
                    f"No good MusicBrainz match for track: {track['title']} (best score: {best_score})"
                )
                return None

            # Update track data
            recording_mbid = best_match["id"]
            updates = {"mbid": recording_mbid, "enriched": 1}

            # Return data for further enrichment if needed
            return {"updates": updates, "mbid": recording_mbid}

        except Exception as e:
            logger.error(f"Error enriching track {track['title']}: {str(e)}")
            return None


class WikidataPlugin(EnricherPlugin):
    """Wikidata enrichment plugin for artist images"""

    def __init__(self, config, db_manager):
        self.config = config
        self.db_manager = db_manager
        self.artwork_path = config["artwork_path"]
        self.session = requests.Session()

        # Rate limiting
        self.last_request_time = 0
        self.request_interval = 1  # 1 request per second

    def _wait_for_rate_limit(self):
        """Wait to respect rate limits"""
        now = time.time()
        elapsed = now - self.last_request_time

        if elapsed < self.request_interval:
            sleep_time = self.request_interval - elapsed
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    def can_enrich_artist(self) -> bool:
        return True

    def can_enrich_album(self) -> bool:
        return False

    def can_enrich_track(self) -> bool:
        return False

    def enrich_artist(self, artist: Dict) -> Optional[Dict]:
        """Enrich artist with image from Wikidata via MusicBrainz"""
        try:
            if not artist.get("mbid"):
                logger.debug(f"No MusicBrainz ID for artist: {artist['name']}")
                return None

            # Get MusicBrainz artist with relations
            artist_mbid = artist["mbid"]

            self._wait_for_rate_limit()
            mb_result = musicbrainzngs.get_artist_by_id(
                artist_mbid, includes=["url-rels"]
            )
            mb_artist = mb_result["artist"]

            # Look for Wikidata relation
            wikidata_url = None
            for relation in mb_artist.get("url-relation-list", []):
                if relation.get("type") == "wikidata":
                    wikidata_url = relation["target"]
                    break

            if not wikidata_url:
                logger.debug(f"No Wikidata link for artist: {artist['name']}")
                return None

            # Extract Wikidata ID (Q number)
            wikidata_id = wikidata_url.split("/")[-1]

            # Query Wikidata API for P18 (image) property
            self._wait_for_rate_limit()
            wikidata_api_url = "https://www.wikidata.org/w/api.php"
            params = {
                "action": "wbgetclaims",
                "entity": wikidata_id,
                "property": "P18",  # P18 is the image property
                "format": "json",
            }

            response = self.session.get(wikidata_api_url, params=params)
            data = response.json()

            # Extract image filename from response
            image_claims = data.get("claims", {}).get("P18", [])
            if not image_claims:
                logger.debug(f"No image found in Wikidata for artist: {artist['name']}")
                return None

            image_filename = image_claims[0]["mainsnak"]["datavalue"]["value"]

            # Format the image URL (Wikimedia Commons)
            # MD5 hash the filename for the URL path
            import hashlib

            filename_md5 = hashlib.md5(
                image_filename.replace(" ", "_").encode("utf-8")
            ).hexdigest()

            image_url = f"https://upload.wikimedia.org/wikipedia/commons/{filename_md5[0]}/{filename_md5[0:2]}/{image_filename.replace(' ', '_')}"

            # Download the image
            self._wait_for_rate_limit()
            image_response = self.session.get(image_url)
            if image_response.status_code != 200:
                logger.error(
                    f"Failed to download image for artist {artist['name']}: {image_response.status_code}"
                )
                return None

            # Save the image in different sizes
            image_data = image_response.content
            self._save_images(image_data, artist["id"], "artist")

            # Update artist data
            updates = {
                "image_url": artist["id"],
            }

            return {"updates": updates}

        except Exception as e:
            logger.error(
                f"Error enriching artist {artist['name']} with Wikidata image: {str(e)}"
            )
            return None

    def enrich_album(self, album: Dict) -> Optional[Dict]:
        """Placeholder for album enrichment - not implemented"""
        return None

    def enrich_track(self, track: Dict) -> Optional[Dict]:
        """Placeholder for track enrichment - not implemented"""
        return None

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


class MetadataEnricher:
    """Main enricher class that processes database entries"""

    def __init__(self, config, db_manager):
        self.config = config
        self.db_manager = db_manager
        self.running = False
        self.lock = threading.Lock()
        self.plugins = []

        # Initialize plugins
        if config["enricher.plugins.musicbrainz.enabled"]:
            self.plugins.append(MusicBrainzPlugin(config, db_manager))

        if config["enricher.plugins.wikidata.enabled"]:
            self.plugins.append(WikidataPlugin(config, db_manager))

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
        for plugin in self.plugins:
            if not plugin.can_enrich_artist():
                continue

            result = plugin.enrich_artist(artist)
            if result and "updates" in result:
                # Apply updates to the database
                self.db_manager.update_artist(artist["id"], result["updates"])

    def _process_albums(self, batch_size=10):
        """Process non-enriched albums"""
        albums = self.db_manager.get_non_enriched_albums(batch_size)

        for album in albums:
            self._enrich_album(album)

    def _enrich_album(self, album):
        """Enrich a single album"""
        for plugin in self.plugins:
            if not plugin.can_enrich_album():
                continue

            result = plugin.enrich_album(album)
            if result and "updates" in result:
                # Apply updates to the database
                self.db_manager.update_album(album["id"], result["updates"])

    def _process_tracks(self, batch_size=50):
        """Process non-enriched tracks"""
        tracks = self.db_manager.get_non_enriched_tracks(batch_size)

        for track in tracks:
            self._enrich_track(track)

    def _enrich_track(self, track):
        """Enrich a single track"""
        for plugin in self.plugins:
            if not plugin.can_enrich_track():
                continue

            result = plugin.enrich_track(track)
            if result and "updates" in result:
                # Apply updates to the database
                self.db_manager.update_track(track["id"], result["updates"])


def _enricher_worker(config, db_manager):
    """Background worker thread for the enricher"""
    global _enricher_instance

    _enricher_instance = MetadataEnricher(config, db_manager)

    # Don't run initial enrichment immediately - wait for indexer to finish first
    time.sleep(30)
    logger.info("Starting initial metadata enrichment")
    _enricher_instance.start()

    logger.info("Metadata enricher thread running")

    # Process queue commands
    while True:
        try:
            # Check for commands
            try:
                command = _enricher_queue.get(timeout=60)  # Check every minute

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


def indexer_callback(changed_items):
    """Callback function to be registered with the indexer"""
    if _enricher_thread and _enricher_thread.is_alive():
        logger.info("Received change notification from indexer")
        _enricher_queue.put({"changed_items": changed_items})
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
        return True
    return False
