import logging
import os
from pathlib import Path
import httpx
import io
import difflib
import re
from PIL import Image
from typing import Dict, Optional

from ..config_model import LocalFilesConfig
from ..utils.name_utils import (
    repair_mojibake,
    space_dotted_abbreviations,
    unescape_web_entities,
)
from .enricher_plugin import (
    EnricherPlugin,
    TransientEnrichmentError,
    inferred_claims,
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


# Fuzzy matching threshold for album and artist matching
FUZZY_MATCH_THRESHOLD = 0.8


class DeezerPlugin(EnricherPlugin):
    """
    Deezer enrichment plugin for artist images

    DISCLAIMER: This plugin is strictly for personal use. All images fetched from
    Deezer are subject to Deezer's terms of use and copyright restrictions.
    This plugin should not be used in any commercial application or publicly
    distributed software without proper licensing from Deezer.
    """

    ENRICHER_VERSION = 1

    def __init__(self, config: LocalFilesConfig, db_manager):
        self.config = config
        self.db_manager = db_manager
        self.artwork_path = Path(config.artwork_path).expanduser().resolve()

        # Set a proper User-Agent
        user_agent = config.enricher.plugins.user_agent
        # Common headers
        self.headers = {"User-Agent": user_agent}

        # Initialize httpx async client for async requests. Serial enrichment
        # used to be Deezer's implicit rate limit; with entities enriched
        # concurrently the connection pool is what keeps us civil — requests
        # past the cap queue instead of leaving together.
        self.async_client = httpx.AsyncClient(
            headers=self.headers,
            limits=httpx.Limits(max_connections=_MAX_CONCURRENT_REQUESTS),
            timeout=_TIMEOUT,
        )

    def can_enrich_artist(self) -> bool:
        return True

    def can_enrich_album(self) -> bool:
        return True

    def can_enrich_track(self) -> bool:
        return False

    def _fuzzy_match_score(self, text1: str, text2: str) -> float:
        """Calculate fuzzy match score between two strings using difflib"""
        if not text1 or not text2:
            return 0.0

        # Normalize strings for comparison
        text1_norm = text1.lower().strip()
        text2_norm = text2.lower().strip()

        # Use SequenceMatcher for fuzzy matching
        matcher = difflib.SequenceMatcher(None, text1_norm, text2_norm)
        return matcher.ratio()

    def _remove_text_in_braces(self, text: str) -> str:
        """Remove text in braces (parentheses, square brackets, curly braces) from album title"""
        # Remove text in parentheses, square brackets, and curly braces
        # This handles cases like "Album Name (Vinyl)", "Album Name [Remaster]", "Album Name {Special Edition}"
        cleaned = re.sub(r"\s*[\(\[\{].*?[\)\]\}]\s*", " ", text)
        # Clean up multiple spaces and strip
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _find_best_album_match(
        self, albums: list, target_title: str, target_artist: str
    ) -> Optional[tuple]:
        """Find the best matching album from a list using fuzzy matching

        Returns: tuple of (album_dict, title_score, artist_score) or None
        """
        best_match = None
        best_score = 0.0

        for album in albums:
            if not album.get("title"):
                continue

            # Calculate album title match score
            title_score = self._fuzzy_match_score(album["title"], target_title)

            # Calculate artist match score if artist info is available
            artist_score = 0.0
            if album.get("artist") and album["artist"].get("name"):
                artist_score = self._fuzzy_match_score(
                    album["artist"]["name"], target_artist
                )

            # For step 1: only consider album title score
            # For step 2: consider both album and artist scores
            album_score = title_score

            logger.debug(
                f"Album '{album['title']}' by '{album.get('artist', {}).get('name', 'Unknown')}' "
                f"- Title score: {title_score:.2f}, Artist score: {artist_score:.2f}"
            )

            if album_score > best_score:
                best_score = album_score
                best_match = (album, title_score, artist_score)

        return best_match

    async def enrich_artist(self, artist: Dict) -> Optional[Dict]:
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

            # Same tag repair the MusicBrainz search does: "В.Цой" and
            # "&amp;" find nothing as-is.
            search_name = space_dotted_abbreviations(
                unescape_web_entities(
                    repair_mojibake(artist["name"], self.config.legacy_tag_encoding)
                )
            )
            search_url = "https://api.deezer.com/search/artist"
            response = await self.async_client.get(
                search_url, params={"q": search_name, "limit": 10}
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

            # Find the best matching artist using fuzzy matching
            best_match = None
            best_score = 0.0

            for deezer_artist in data["data"]:
                if not deezer_artist.get("name"):
                    continue

                # Calculate artist name match score
                name_score = self._fuzzy_match_score(
                    deezer_artist["name"], search_name
                )

                logger.debug(
                    f"Artist '{deezer_artist['name']}' - Name score: {name_score:.2f}"
                )

                if name_score > best_score and name_score > FUZZY_MATCH_THRESHOLD:
                    best_score = name_score
                    best_match = deezer_artist

            # If no good match found
            if not best_match:
                logger.debug(
                    f"No artist with score > {FUZZY_MATCH_THRESHOLD} found for: {artist['name']}"
                )
                return None

            deezer_artist = best_match
            logger.debug(
                f"Best artist match for '{artist['name']}': '{deezer_artist['name']}' (score: {best_score:.2f})"
            )

            # Check if the artist has an image
            if "picture_xl" not in deezer_artist or not deezer_artist["picture_xl"]:
                logger.debug(
                    f"No image available for artist on Deezer: {deezer_artist['name']}"
                )
                return None

            # Download the image. A redirect is the CDN pointing at its
            # generic placeholder ("no real photo") — skipping it leaves the
            # artist open for a later source, so it's not an error.
            image_response = await self.async_client.get(deezer_artist["picture_xl"])
            if image_response.is_redirect:
                logger.debug(
                    f"Deezer has only a placeholder image for artist {artist['name']}"
                )
                return None
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

                logger.debug(f"Added image from Deezer for artist: {artist['name']}")
                return {"updates": updates}
            else:
                return None

        except httpx.TransportError as e:
            raise TransientEnrichmentError(f"Deezer is unreachable: {e}") from e
        except Exception as e:
            logger.error(
                f"Error enriching artist {artist['name']} with Deezer image: {str(e)}"
            )
            return None

    def _save_images(self, image_data: bytes, entity_id: str, entity_type: str) -> bool:
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
            thumbnail.thumbnail((50, 50), Image.Resampling.LANCZOS)
            thumbnail.save(
                os.path.join(dir_path, f"{entity_id}_thumbnail.jpg"), "JPEG", quality=90
            )

            # Save small (230x230)
            small = img.copy()
            small.thumbnail((230, 230), Image.Resampling.LANCZOS)
            small.save(
                os.path.join(dir_path, f"{entity_id}_small.jpg"), "JPEG", quality=90
            )

            # Save large (600x600 or original if smaller)
            large = img.copy()
            large.thumbnail((600, 600), Image.Resampling.LANCZOS)
            large.save(
                os.path.join(dir_path, f"{entity_id}_large.jpg"), "JPEG", quality=90
            )

            return True
        except Exception as e:
            logger.error(
                f"Error saving artwork for {entity_type} {entity_id}: {str(e)}"
            )
            return False

    async def enrich_album(self, album: Dict) -> Optional[Dict]:
        """
        Enrich album with cover artwork from Deezer API using improved 3-step search

        Uses a three-step approach:
        1. Structured search with fuzzy matching on title only
        2. Simple search with fuzzy matching on both album and artist
        3. Cleaned search (removing braces) with fuzzy matching on both
        """
        # Skip if album is already enriched or has an image_url
        if album.get("enriched") or album.get("image_url"):
            logger.debug(
                f"Album {album['title']} already has cover art or is enriched, skipping Deezer lookup"
            )
            return None

        try:
            # Get artist name from various possible sources
            artist_name = await self._get_artist_name(album)
            if not artist_name:
                logger.error(f"Could not find artist name for album: {album['title']}")
                return None

            logger.info(
                f"Searching for album cover on Deezer: {album['title']} by {artist_name}"
            )

            # Try each search step in order
            result = await self._search_step_1_structured(album, artist_name)
            if result:
                return result

            result = await self._search_step_2_simple(album, artist_name)
            if result:
                return result

            result = await self._search_step_3_cleaned(album, artist_name)
            if result:
                return result

            logger.info(
                f"No good matches found for album: {album['title']} by {artist_name}"
            )
            return None

        except httpx.TransportError as e:
            raise TransientEnrichmentError(f"Deezer is unreachable: {e}") from e
        except Exception as e:
            logger.error(
                f"Error enriching album {album['title']} with Deezer cover: {str(e)}"
            )
            return None

    async def _get_artist_name(self, album: Dict) -> Optional[str]:
        """Get artist name from album data"""
        if "artist_name" in album:
            return album["artist_name"]
        elif "artist_id" in album:
            artist = await self.db_manager.get_artist_by_id(album["artist_id"])
            if artist and "name" in artist:
                return artist["name"]
        return None

    async def _search_step_1_structured(
        self, album: Dict, artist_name: str
    ) -> Optional[Dict]:
        """Step 1: Structured search with fuzzy matching on title only"""
        logger.debug(
            f"Step 1: Structured search for '{album['title']}' by '{artist_name}'"
        )

        search_url = "https://api.deezer.com/search/album"
        response = await self.async_client.get(
            search_url,
            params={
                "q": f"artist:{artist_name} album:{album['title']}",
                "limit": 5,
            },
        )

        if response.status_code != 200:
            logger.error(
                f"Step 1: Failed to search Deezer for album {album['title']}: {response.status_code}"
            )
            return None

        data = response.json()
        if not ("data" in data and data["data"]):
            return None

        # Find best match using fuzzy matching
        best_match = self._find_best_album_match(
            data["data"], album["title"], artist_name
        )

        if not best_match:
            return None

        deezer_album, title_score, artist_score = best_match

        # If title score is > 0.9, use this result
        if title_score > FUZZY_MATCH_THRESHOLD:
            logger.info(
                f"Step 1: Found good match for '{album['title']}' - "
                f"'{deezer_album['title']}' (score: {title_score:.2f})"
            )
            return await self._process_album_match(deezer_album, album, artist_name)

        return None

    async def _search_step_2_simple(
        self, album: Dict, artist_name: str
    ) -> Optional[Dict]:
        """Step 2: Simple search with fuzzy matching on both album and artist"""
        logger.debug(f"Step 2: Simple search for '{album['title']}'")

        search_url = "https://api.deezer.com/search/album"
        response = await self.async_client.get(
            search_url,
            params={
                "q": album["title"],
                "limit": 10,
            },
        )

        if response.status_code != 200:
            logger.error(
                f"Step 2: Failed simple search on Deezer for album {album['title']}: {response.status_code}"
            )
            return None

        data = response.json()
        if not ("data" in data and data["data"]):
            return None

        # Find best match considering both album and artist scores
        for deezer_album in data["data"]:
            if not deezer_album.get("title"):
                continue

            title_score = self._fuzzy_match_score(deezer_album["title"], album["title"])
            artist_score = 0.0

            if deezer_album.get("artist") and deezer_album["artist"].get("name"):
                artist_score = self._fuzzy_match_score(
                    deezer_album["artist"]["name"], artist_name
                )

            logger.debug(
                f"Step 2: Album '{deezer_album['title']}' by '{deezer_album.get('artist', {}).get('name', 'Unknown')}' "
                f"- Title: {title_score:.2f}, Artist: {artist_score:.2f}"
            )

            # Both scores need to be >= 0.9
            if (
                title_score >= FUZZY_MATCH_THRESHOLD
                and artist_score >= FUZZY_MATCH_THRESHOLD
            ):
                logger.info(
                    f"Step 2: Found excellent match for '{album['title']}' by '{artist_name}' - "
                    f"'{deezer_album['title']}' by '{deezer_album['artist']['name']}' "
                    f"(title: {title_score:.2f}, artist: {artist_score:.2f})"
                )
                return await self._process_album_match(deezer_album, album, artist_name)

        return None

    async def _search_step_3_cleaned(
        self, album: Dict, artist_name: str
    ) -> Optional[Dict]:
        """Step 3: Search with cleaned album title (removing text in braces)"""
        cleaned_album_title = self._remove_text_in_braces(album["title"])

        # Only try step 3 if the cleaned title is different from the original
        if cleaned_album_title == album["title"] or not cleaned_album_title.strip():
            return None

        logger.debug(
            f"Step 3: Cleaned title search for '{cleaned_album_title}' (original: '{album['title']}')"
        )

        search_url = "https://api.deezer.com/search/album"
        response = await self.async_client.get(
            search_url,
            params={
                "q": cleaned_album_title,
                "limit": 15,
            },
        )

        if response.status_code != 200:
            logger.error(
                f"Step 3: Failed cleaned search on Deezer for album {album['title']}: {response.status_code}"
            )
            return None

        data = response.json()
        if not ("data" in data and data["data"]):
            return None

        # Find best match considering both album and artist scores with cleaned title
        for deezer_album in data["data"]:
            if not deezer_album.get("title"):
                continue

            # Compare with cleaned titles for both
            cleaned_deezer_title = self._remove_text_in_braces(deezer_album["title"])
            title_score = self._fuzzy_match_score(
                cleaned_deezer_title, cleaned_album_title
            )
            artist_score = 0.0

            if deezer_album.get("artist") and deezer_album["artist"].get("name"):
                artist_score = self._fuzzy_match_score(
                    deezer_album["artist"]["name"], artist_name
                )

            logger.debug(
                f"Step 3: Album '{deezer_album['title']}' (cleaned: '{cleaned_deezer_title}') "
                f"by '{deezer_album.get('artist', {}).get('name', 'Unknown')}' "
                f"- Title: {title_score:.2f}, Artist: {artist_score:.2f}"
            )

            # Both scores need to be > 0.9
            if (
                title_score > FUZZY_MATCH_THRESHOLD
                and artist_score > FUZZY_MATCH_THRESHOLD
            ):
                logger.info(
                    f"Step 3: Found excellent match with cleaned title for '{album['title']}' by '{artist_name}' - "
                    f"'{deezer_album['title']}' by '{deezer_album['artist']['name']}' "
                    f"(cleaned title: {title_score:.2f}, artist: {artist_score:.2f})"
                )
                return await self._process_album_match(deezer_album, album, artist_name)

        return None

    async def _process_album_match(
        self, deezer_album: Dict, album: Dict, artist_name: str
    ) -> Optional[Dict]:
        """Process a matched Deezer album and download artwork"""
        # Check if the album has a cover
        if "cover_xl" not in deezer_album or not deezer_album["cover_xl"]:
            logger.debug(f"No cover available for album on Deezer: {album['title']}")
            return None

        # Download the cover
        image_response = await self.async_client.get(deezer_album["cover_xl"])
        if image_response.status_code != 200:
            logger.error(
                f"Failed to download cover for album {album['title']}: {image_response.status_code}"
            )
            return None

        updates = {}

        # Save the image in different sizes. The cover is Deezer's only direct
        # write — genre is a resolvable field, so it's emitted as a claim and
        # left to resolution (a local tag or MB outranks it).
        image_data = image_response.content
        if self._save_images(image_data, album["id"], "album"):
            updates["image_url"] = album["id"]
            logger.info(
                f"Added cover image from Deezer for album: {album['title']} by {artist_name}"
            )

        # Add genre if available from Deezer
        genre = None
        if "genre_id" in deezer_album and deezer_album.get("genre_id"):
            # Get detailed genre info
            try:
                genre_response = await self.async_client.get(
                    f"https://api.deezer.com/genre/{deezer_album['genre_id']}"
                )
                if genre_response.status_code == 200:
                    genre = genre_response.json().get("name")
            except Exception as genre_error:
                logger.warning(
                    f"Error fetching genre for album {album['title']}: {str(genre_error)}"
                )

        claims = inferred_claims(f"deezer:{deezer_album.get('id', '')}", {"genre": genre})
        return {"updates": updates, "claims": claims}

    async def enrich_track(self, track: Dict) -> Optional[Dict]:
        """Placeholder for track enrichment - not implemented"""
        return None
