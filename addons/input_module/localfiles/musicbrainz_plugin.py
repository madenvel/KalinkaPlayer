import logging
import time
import musicbrainzngs
from typing import Dict, Optional

from .enricher_plugin import EnricherPlugin

logger = logging.getLogger(__name__.split(".")[-1])


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
