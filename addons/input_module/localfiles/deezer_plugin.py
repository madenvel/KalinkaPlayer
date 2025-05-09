import logging
import time
import os
import requests
import io
from PIL import Image
from typing import Dict, Optional

from .enricher_plugin import EnricherPlugin

logger = logging.getLogger(__name__.split(".")[-1])


class DeezerPlugin(EnricherPlugin):
    """
    Deezer enrichment plugin for artist images

    DISCLAIMER: This plugin is strictly for personal use. All images fetched from
    Deezer are subject to Deezer's terms of use and copyright restrictions.
    This plugin should not be used in any commercial application or publicly
    distributed software without proper licensing from Deezer.
    """

    def __init__(self, config, db_manager):
        self.config = config
        self.db_manager = db_manager
        self.artwork_path = config["artwork_path"]
        self.session = requests.Session()

        # Set a proper User-Agent
        user_agent = config.get(
            "enricher.plugins.deezer.user_agent",
            "RpiPlayer/1.0 (https://github.com/madenvel/KalinkaPlayer; envelsavinds@gmail.com)",
        )
        self.session.headers.update({"User-Agent": user_agent})

        # Rate limiting
        self.last_request_time = 0
        self.request_interval = 1 / 10  # 10 requests per second to be respectful

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
        return False

    def enrich_artist(self, artist: Dict) -> Optional[Dict]:
        """
        Enrich artist with image from Deezer API

        This will only run if the artist doesn't already have an image from
        previous enrichment plugins (like Wikidata)
        """
        # Skip if artist already has an image_url
        if artist.get("image_url"):
            logger.debug(
                f"Artist {artist['name']} already has an image, skipping Deezer lookup"
            )
            return None

        try:
            logger.debug(f"Searching for artist image on Deezer: {artist['name']}")

            # Search for artist on Deezer
            self._wait_for_rate_limit()
            search_url = f"https://api.deezer.com/search/artist"
            response = self.session.get(
                search_url, params={"q": artist["name"], "limit": 10}
            )

            if response.status_code != 200:
                logger.error(
                    f"Failed to search Deezer for {artist['name']}: {response.status_code}"
                )
                return None

            data = response.json()
            if "data" not in data or not data["data"]:
                logger.debug(f"No Deezer results found for artist: {artist['name']}")
                return None

            # Get the first result - assuming best match comes first
            deezer_artist = data["data"][0]

            # Check if the artist has an image
            if "picture_xl" not in deezer_artist or not deezer_artist["picture_xl"]:
                logger.debug(
                    f"No image available for artist on Deezer: {artist['name']}"
                )
                return None

            # Download the image
            self._wait_for_rate_limit()
            image_response = self.session.get(deezer_artist["picture_xl"])
            if image_response.status_code != 200:
                logger.error(
                    f"Failed to download image for artist {artist['name']}: {image_response.status_code}"
                )
                return None

            # Save the image in different sizes
            image_data = image_response.content
            if self._save_images(image_data, artist["id"], "artist"):
                # Update artist data
                updates = {
                    "image_url": artist["id"],
                }

                logger.info(f"Added image from Deezer for artist: {artist['name']}")
                return {"updates": updates}
            else:
                return None

        except Exception as e:
            logger.error(
                f"Error enriching artist {artist['name']} with Deezer image: {str(e)}"
            )
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

    def enrich_album(self, album: Dict) -> Optional[Dict]:
        """
        Enrich album with cover artwork from Deezer API

        This will only run if the album doesn't already have an image_url
        """
        # Skip if album is already enriched or has an image_url
        if album.get("enriched") or album.get("image_url"):
            logger.debug(
                f"Album {album['title']} already has cover art or is enriched, skipping Deezer lookup"
            )
            return None

        try:
            artist_name = None

            # Try to get artist name from various possible sources
            if "artist_name" in album:
                artist_name = album["artist_name"]
            elif "artist_id" in album:
                artist = self.db_manager.get_artist_by_id(album["artist_id"])
                if artist and "name" in artist:
                    artist_name = artist["name"]

            if not artist_name:
                logger.error(f"Could not find artist name for album: {album['title']}")
                return None

            logger.debug(
                f"Searching for album cover on Deezer: {album['title']} by {artist_name}"
            )

            # Search for album on Deezer
            self._wait_for_rate_limit()
            search_url = "https://api.deezer.com/search/album"
            response = self.session.get(
                search_url,
                params={
                    "q": f"artist:'{artist_name}' album:'{album['title']}'",
                    "limit": 5,
                },
            )

            if response.status_code != 200:
                logger.error(
                    f"Failed to search Deezer for album {album['title']}: {response.status_code}"
                )
                return None

            data = response.json()
            if "data" not in data or not data["data"]:
                logger.debug(f"No Deezer results found for album: {album['title']}")
                return None

            # Get the first result - assuming best match comes first
            deezer_album = data["data"][0]

            # Check if the album has a cover
            if "cover_xl" not in deezer_album or not deezer_album["cover_xl"]:
                logger.debug(
                    f"No cover available for album on Deezer: {album['title']}"
                )
                return None

            # Download the cover
            self._wait_for_rate_limit()
            image_response = self.session.get(deezer_album["cover_xl"])
            if image_response.status_code != 200:
                logger.error(
                    f"Failed to download cover for album {album['title']}: {image_response.status_code}"
                )
                return None

            # Save the image in different sizes
            image_data = image_response.content
            if self._save_images(image_data, album["id"], "album"):
                # Update album data
                updates = {
                    "image_url": album["id"],
                }

                # Add genre if available from Deezer
                if "genre_id" in deezer_album and deezer_album.get("genre_id"):
                    # Get detailed genre info
                    self._wait_for_rate_limit()
                    try:
                        genre_response = self.session.get(
                            f"https://api.deezer.com/genre/{deezer_album['genre_id']}"
                        )
                        if genre_response.status_code == 200:
                            genre_data = genre_response.json()
                            if "name" in genre_data:
                                updates["genre"] = genre_data["name"]
                                logger.debug(
                                    f"Added genre from Deezer for album {album['title']}: {genre_data['name']}"
                                )
                    except Exception as genre_error:
                        logger.warning(
                            f"Error fetching genre for album {album['title']}: {str(genre_error)}"
                        )

                logger.info(
                    f"Added cover image from Deezer for album: {album['title']} by {artist_name}"
                )
                return {"updates": updates}
            else:
                return None

        except Exception as e:
            logger.error(
                f"Error enriching album {album['title']} with Deezer cover: {str(e)}"
            )
            return None

    def enrich_track(self, track: Dict) -> Optional[Dict]:
        """Placeholder for track enrichment - not implemented"""
        return None
