import logging
from pathlib import Path
from typing import Dict, Optional

import httpx

from ..config_model import LocalFilesConfig
from ..utils.artwork_store import save_artwork_images
from .enricher_plugin import (
    EnricherPlugin,
    TransientEnrichmentError,
    raise_if_service_unavailable,
)

logger = logging.getLogger(__name__.split(".")[-1])

# Requests above the cap queue on the pool under their own long budget (see
# deezer_plugin); the read budget is generous because archive.org is slow.
_MAX_CONCURRENT_REQUESTS = 4
_TIMEOUT = httpx.Timeout(15.0, pool=120.0)

# The archive's pre-rendered thumbnail closest above our largest stored size
# (600px): full-resolution scans can run to many megabytes.
_FRONT_URL = "https://coverartarchive.org/release/{mbid}/front-1200"


class CoverArtArchivePlugin(EnricherPlugin):
    """Album covers from the Cover Art Archive, keyed by the MusicBrainz
    release id an earlier plugin resolved.

    Runs after the fetching sources and before the procedural generator: it
    only fills albums that still have no real cover, and it treats a
    generated placeholder as absence, so a later pass can upgrade it. This
    is the cover source for music Deezer does not carry — free/CC releases
    that MusicBrainz nonetheless catalogues.
    """

    ENRICHER_VERSION = 1

    def __init__(self, config: LocalFilesConfig, db_manager):
        self.config = config
        self.db_manager = db_manager
        self.artwork_path = Path(config.artwork_path).expanduser().resolve()
        self.async_client = httpx.AsyncClient(
            headers={"User-Agent": config.enricher.plugins.user_agent},
            limits=httpx.Limits(max_connections=_MAX_CONCURRENT_REQUESTS),
            timeout=_TIMEOUT,
            follow_redirects=True,
        )

    def can_enrich_artist(self) -> bool:
        return False  # the archive stores release art only

    def can_enrich_album(self) -> bool:
        return True

    def can_enrich_track(self) -> bool:
        return False

    async def enrich_artist(self, artist: Dict) -> Optional[Dict]:
        return None

    async def enrich_track(self, track: Dict) -> Optional[Dict]:
        return None

    async def enrich_album(self, album: Dict) -> Optional[Dict]:
        if album.get("image_url") and not album.get("image_generated"):
            return None
        mbid = album.get("mbid")
        if not mbid:
            return None

        try:
            response = await self.async_client.get(_FRONT_URL.format(mbid=mbid))
        except httpx.HTTPError as e:
            raise TransientEnrichmentError(
                f"Cover Art Archive is unreachable: {e}"
            ) from e

        raise_if_service_unavailable(response.status_code, "Cover Art Archive")
        if response.status_code == 404:
            logger.debug(f"No archived cover for release {mbid}")
            return None
        if response.status_code != 200:
            logger.warning(
                f"Cover Art Archive returned {response.status_code} for {mbid}"
            )
            return None

        if not save_artwork_images(
            self.artwork_path, response.content, album["id"], "album"
        ):
            return None
        logger.info(
            f"Added cover from the Cover Art Archive for album: "
            f"{album.get('title', album['id'])}"
        )
        return {"updates": {"image_url": album["id"], "image_generated": 0}}
