import asyncio
import logging
from pathlib import Path
import httpx
import musicbrainzngs
from typing import Dict, Optional

from ..config_model import LocalFilesConfig
from ..utils.artwork_store import save_artwork_images
from .mb_client import mb_call, set_user_agent
from .enricher_plugin import (
    EnricherPlugin,
    TransientEnrichmentError,
    raise_if_service_unavailable,
    raise_musicbrainz_unreachable,
)


logger = logging.getLogger(__name__.split(".")[-1])

# Outbound requests in flight at once. The enricher processes several
# entities concurrently; this keeps a burst from reaching the service
# all at once without needing a limiter at each call site.
_MAX_CONCURRENT_REQUESTS = 4

# Waiting for one of those connections is queueing, not a service problem, so
# it gets a long budget of its own: the plugins report a timeout as "service
# unreachable", which ends the whole enrichment pass.
_TIMEOUT = httpx.Timeout(5.0, pool=120.0)


class WikidataPlugin(EnricherPlugin):
    """Wikidata enrichment plugin for artist images"""

    # 2: image lookups never completed at all — the module called
    # raise_if_service_unavailable without importing it — so every artist
    # this plugin was asked about deserves another attempt.
    ENRICHER_VERSION = 2

    def __init__(self, config: LocalFilesConfig, db_manager):
        self.config = config
        self.db_manager = db_manager
        self.artwork_path = Path(config.artwork_path).expanduser().resolve()

        # Wikimedia answers an unidentified client with 403, and MusicBrainz
        # — reached here for the Wikidata link — refuses it outright. The
        # latter is module-wide state, so it cannot be left to whether the
        # MusicBrainz plugin happens to be enabled.
        user_agent = config.enricher.plugins.user_agent
        set_user_agent(user_agent)

        # The connection cap is the rate limit now that entities are enriched
        # concurrently: requests past it queue rather than hitting Wikimedia
        # all at once.
        self.async_client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            limits=httpx.Limits(max_connections=_MAX_CONCURRENT_REQUESTS),
            timeout=_TIMEOUT,
        )

    def can_enrich_artist(self) -> bool:
        return True

    def can_enrich_album(self) -> bool:
        return False

    def can_enrich_track(self) -> bool:
        return False

    async def enrich_artist(self, artist: Dict) -> Optional[Dict]:
        """Enrich artist with image from Wikidata via MusicBrainz"""
        try:
            if not artist.get("mbid"):
                logger.debug(f"No MusicBrainz ID for artist: {artist['name']}")
                return None

            # Get MusicBrainz artist with relations
            artist_mbid = artist["mbid"]

            mb_result = await mb_call(
                musicbrainzngs.get_artist_by_id,
                artist_mbid,
                includes=["url-rels"],
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
            wikidata_api_url = "https://www.wikidata.org/w/api.php"
            params = {
                "action": "wbgetclaims",
                "entity": wikidata_id,
                "property": "P18",  # P18 is the image property
                "format": "json",
            }

            response = await self.async_client.get(wikidata_api_url, params=params)
            raise_if_service_unavailable(response.status_code, "Wikidata")
            data = response.json()

            # Extract image filename from response
            image_claims = data.get("claims", {}).get("P18", [])
            if not image_claims:
                logger.debug(f"No image found in Wikidata for artist: {artist['name']}")
                return None

            image_filename = image_claims[0]["mainsnak"]["datavalue"]["value"]

            # Get proper image URL using the MediaWiki API
            image_url = await self._get_wikimedia_image_url(image_filename)
            if not image_url:
                logger.error(
                    f"Failed to construct image URL for artist {artist['name']}"
                )
                return None

            # Download the image
            image_response = await self.async_client.get(image_url)
            raise_if_service_unavailable(image_response.status_code, "Wikimedia")
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

        except TransientEnrichmentError:
            raise
        except httpx.TransportError as e:
            raise TransientEnrichmentError(f"Wikidata is unreachable: {e}") from e
        except musicbrainzngs.NetworkError as e:
            raise_musicbrainz_unreachable(e)
            logger.error(
                f"Error enriching artist {artist['name']} with Wikidata image: {str(e)}"
            )
            return None
        except Exception as e:
            logger.error(
                f"Error enriching artist {artist['name']} with Wikidata image: {str(e)}"
            )
            return None

    async def _get_wikimedia_image_url(self, image_filename: str) -> Optional[str]:
        """Get the proper URL for a Wikimedia Commons image using the MediaWiki API"""
        try:
            # Use the MediaWiki API to get the proper URL
            commons_api_url = "https://commons.wikimedia.org/w/api.php"
            params = {
                "action": "query",
                "titles": f"File:{image_filename}",
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
            }

            response = await self.async_client.get(commons_api_url, params=params)
            raise_if_service_unavailable(response.status_code, "Wikimedia")
            if response.status_code != 200:
                logger.error(
                    f"Failed to query MediaWiki API for image {image_filename}: {response.status_code}"
                )
                return None

            data = response.json()

            # Extract image URL from response
            pages = data.get("query", {}).get("pages", {})
            if not pages:
                return None

            # Get the first (and only) page
            page = next(iter(pages.values()))

            if "imageinfo" not in page or not page["imageinfo"]:
                return None

            return page["imageinfo"][0]["url"]

        except (httpx.TransportError, TransientEnrichmentError):
            raise
        except Exception as e:
            logger.error(
                f"Error getting Wikimedia image URL for {image_filename}: {str(e)}"
            )
            return None

    async def enrich_album(self, album: Dict) -> Optional[Dict]:
        """Placeholder for album enrichment - not implemented"""
        return None

    async def enrich_track(self, track: Dict) -> Optional[Dict]:
        """Placeholder for track enrichment - not implemented"""
        return None

    def _save_images(self, image_data: bytes, entity_id: str, entity_type: str):
        return save_artwork_images(
            self.artwork_path, image_data, entity_id, entity_type
        )
