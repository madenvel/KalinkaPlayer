import logging
import time
import os
import requests
import io
import hashlib
import musicbrainzngs
from PIL import Image
from typing import Dict, Optional

from .enricher_plugin import EnricherPlugin

logger = logging.getLogger(__name__.split(".")[-1])


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
