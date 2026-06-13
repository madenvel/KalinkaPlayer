import asyncio
import logging
import re
from typing import List, Optional

import httpx
from pydantic import PositiveInt

from kalinka_plugin_sdk.datamodel import (
    Album,
    Artist,
    BrowseItem,
    BrowseItemList,
    CardSize,
    Catalog,
    CatalogRole,
    CoverImage,
    EmptyList,
    EntityId,
    EntityType,
    FavoriteIds,
    GenreList,
    Owner,
    Playlist,
    Preview,
    PreviewContentType,
    PreviewType,
    Track,
)
from kalinka_plugin_sdk.inputmodule import (
    InputModule,
    SearchType,
    TrackInfo,
    TrackUrl,
)

from .config_model import JamendoConfig

logger = logging.getLogger(__name__.split(".")[-1])

SOURCE = "jamendo"
BASE_URL = "https://api.jamendo.com/v3.0/"

# Jamendo caps page size at 200.
MAX_LIMIT = 200

# Map the user-facing audio quality label (config.audio_format, stored as the
# enum *value* because the config uses use_enum_values=True) to Jamendo's
# audioformat code and the MIME type we report back to the player.
FORMAT_CODE = {
    "MP3 (VBR ~V0)": "mp32",
    "MP3 (96 kbps)": "mp31",
    "OGG Vorbis": "ogg",
    "FLAC": "flac",
}
FORMAT_MIME = {
    "mp31": "audio/mpeg",
    "mp32": "audio/mpeg",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
}


# ---------------------------------------------------------------------------
# EntityId helpers
# ---------------------------------------------------------------------------


def artist_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.ARTIST, source=SOURCE)


def album_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.ALBUM, source=SOURCE)


def track_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.TRACK, source=SOURCE)


def playlist_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.PLAYLIST, source=SOURCE)


def user_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.USER, source=SOURCE)


def catalog_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.CATALOG, source=SOURCE)


# ---------------------------------------------------------------------------
# HTTP transport with retry on transient failures (mirrors the qobuz plugin)
# ---------------------------------------------------------------------------


class RetryTransport(httpx.AsyncHTTPTransport):
    def __init__(self, read_retries=3, **kwargs):
        super().__init__(**kwargs)
        self.read_retries = read_retries
        self.backoff_factor = 0.5

    async def handle_async_request(self, request) -> httpx.Response:
        read_retries = 0
        last_exception = None
        while read_retries < self.read_retries:
            try:
                response = await super().handle_async_request(request)
                if 500 <= response.status_code < 600:
                    read_retries += 1
                    await asyncio.sleep(self.backoff_factor * read_retries)
                    logger.warning(
                        f"Retry {read_retries} due to status code={response.status_code}"
                    )
                    continue
                return response
            except (
                httpx.ProtocolError,
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.ConnectTimeout,
                httpx.ProxyError,
                httpx.ReadError,
            ) as exc:
                last_exception = exc
                read_retries += 1
                await asyncio.sleep(self.backoff_factor * read_retries)
                logger.warning(f"Retry {read_retries} due to {exc}")
        if last_exception is not None:
            raise last_exception
        raise RuntimeError("handle_async_request failed without exception")


class JamendoClient:
    """Thin async wrapper over the Jamendo v3.0 REST API.

    Only a ``client_id`` is needed for search/browse/playback. Favourites and
    playlist mutation would require an OAuth2 user flow and are out of scope.
    """

    def __init__(self, client_id: str):
        self.client_id = client_id
        self.base = BASE_URL
        self.session = httpx.AsyncClient(
            transport=RetryTransport(read_retries=3, retries=3),
            timeout=10,
        )
        self.session.headers.update(
            {
                "User-Agent": "kalinka-plugin-jamendo",
            }
        )

    async def aclose(self) -> None:
        await self.session.aclose()

    async def request(self, path: str, params: dict) -> list:
        """GET an endpoint and return its ``results`` array.

        Jamendo wraps every response in ``{"headers": {...}, "results": [...]}``.
        A failed call still returns HTTP 200 with ``headers.status == "failed"``;
        we log and return an empty list so the UI degrades to "no results"
        rather than throwing.
        """
        merged = {"client_id": self.client_id, "format": "json", **params}
        response = await self.session.get(self.base + path, params=merged)

        if not response.is_success:
            logger.warning(
                "Jamendo %s failed: HTTP %s", path, response.status_code
            )
            return []

        rjson = response.json()
        headers = rjson.get("headers", {})
        if headers.get("status") != "success":
            logger.warning(
                "Jamendo %s error: %s",
                path,
                headers.get("error_message") or headers,
            )
            return []

        return rjson.get("results", [])


async def get_client(config: JamendoConfig) -> JamendoClient:
    if not config.client_id:
        logger.warning(
            "Jamendo client_id is not configured — requests will return empty."
        )
    return JamendoClient(config.client_id)


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def _resize(url: Optional[str], width: int) -> Optional[str]:
    """Return ``url`` requesting a specific image width.

    Jamendo image URLs carry a ``width=`` query param we can rewrite to get
    different sizes from the single URL the API returns.
    """
    if not url:
        return None
    if "width=" in url:
        return re.sub(r"width=\d+", f"width={width}", url)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}width={width}"


def _cover(url: Optional[str]) -> Optional[CoverImage]:
    if not url:
        return None
    return CoverImage(
        thumbnail=_resize(url, 100),
        small=_resize(url, 200),
        large=_resize(url, 600),
    )


def _estimated_total(offset: int, limit: int, count: int) -> int:
    """Estimate a result total.

    Jamendo never reports a grand total — only the size of the current page.
    Report ``offset + count``, and when the page came back full assume there's
    at least one more page so clients keep paginating.
    """
    total = offset + count
    if limit and count >= limit:
        total += limit
    return total


# ---------------------------------------------------------------------------
# Preview configs reused across catalog sections
# ---------------------------------------------------------------------------


def _album_tracks_section(aid: str) -> BrowseItem:
    return BrowseItem(
        id=album_id(aid),
        name="Tracks",
        can_browse=True,
        can_add=True,
        catalog=Catalog(
            id=album_id(aid),
            title="Tracks",
            can_genre_filter=False,
            preview_config=Preview(
                type=PreviewType.TILE_NUMBERED,
                content_type=PreviewContentType.TRACK,
                items_count=10,
                rows_count=1,
                aspect_ratio=1.0,
                card_size=CardSize.SMALL,
            ),
        ),
    )


def _artist_albums_section(aid: str) -> BrowseItem:
    return BrowseItem(
        id=artist_id(aid),
        name="Albums",
        can_browse=True,
        can_add=False,
        catalog=Catalog(
            id=artist_id(aid),
            title="Albums",
            can_genre_filter=False,
            preview_config=Preview(
                type=PreviewType.TILE,
                content_type=PreviewContentType.ALBUM,
                items_count=10,
                rows_count=1,
                aspect_ratio=1.0,
                card_size=CardSize.SMALL,
            ),
        ),
    )


def _playlist_tracks_section(pid: str) -> BrowseItem:
    return BrowseItem(
        id=playlist_id(pid),
        name="Tracks",
        can_browse=True,
        can_add=True,
        catalog=Catalog(
            id=playlist_id(pid),
            title="Tracks",
            can_genre_filter=False,
            preview_config=Preview(
                type=PreviewType.TILE,
                content_type=PreviewContentType.TRACK,
                items_count=15,
                rows_count=1,
                aspect_ratio=1.0,
                card_size=CardSize.SMALL,
            ),
        ),
    )


class JamendoInputModule(InputModule):
    def __init__(self, config: JamendoConfig, client: JamendoClient):
        self.client = client
        # config.audio_format is the enum *value* (use_enum_values=True).
        self.audio_format = FORMAT_CODE.get(config.audio_format, "mp32")
        self.audio_mime = FORMAT_MIME[self.audio_format]
        logger.info("Jamendo audio format: %s", self.audio_format)

    def module_name(self) -> str:
        return "Jamendo"

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self, type: SearchType, query: str, offset=0, limit=50
    ) -> BrowseItemList:
        limit = min(limit, MAX_LIMIT)
        endpoint = type.value + "s"  # track -> tracks, album -> albums, ...
        params = {"namesearch": query, "offset": offset, "limit": limit}
        if type == SearchType.track:
            params["audioformat"] = self.audio_format
        results = await self.client.request(endpoint, params)

        if type == SearchType.track:
            items = self._tracks_to_browse_items(results)
        elif type == SearchType.album:
            items = self._albums_to_browse_items(results)
        elif type == SearchType.artist:
            items = self._artists_to_browse_items(results)
        elif type == SearchType.playlist:
            items = self._playlists_to_browse_items(results)
        else:
            return EmptyList(offset, limit)

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=_estimated_total(offset, limit, len(items)),
            items=items,
        )

    # ------------------------------------------------------------------
    # Browse
    # ------------------------------------------------------------------

    async def browse(
        self,
        entity_id: EntityId,
        offset: PositiveInt = 0,
        limit: PositiveInt = 50,
        genre_ids: List[EntityId] = [],
    ) -> BrowseItemList:
        limit = min(limit, MAX_LIMIT)
        if entity_id.type == EntityType.ALBUM:
            return await self._browse_album(entity_id.id, offset, limit)
        elif entity_id.type == EntityType.ARTIST:
            return await self._browse_artist(entity_id.id, offset, limit)
        elif entity_id.type == EntityType.PLAYLIST:
            return await self._browse_playlist(entity_id.id, offset, limit)
        elif entity_id.type == EntityType.CATALOG:
            return await self._browse_catalog(entity_id.id, offset, limit)
        return EmptyList(offset, limit)

    async def _browse_album(
        self, id: str, offset: int, limit: int
    ) -> BrowseItemList:
        results = await self.client.request(
            "albums/tracks",
            {
                "id": id,
                "track_type": "albumtrack",
                "audioformat": self.audio_format,
            },
        )
        if not results:
            return EmptyList(offset, limit)
        tracks = results[0].get("tracks", [])
        page = tracks[offset : offset + limit]
        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=len(tracks),
            items=self._tracks_to_browse_items(page, album_meta=results[0]),
        )

    async def _browse_artist(
        self, id: str, offset: int, limit: int
    ) -> BrowseItemList:
        # offset/limit on artists/albums paginate the *artist* list, not the
        # nested albums — so we fetch the artist's albums and paginate them
        # client-side (same approach as album tracks / playlist tracks).
        results = await self.client.request("artists/albums", {"id": id})
        if not results:
            return EmptyList(offset, limit)
        albums = results[0].get("albums", [])
        # Stamp the parent artist so album cards carry the artist name.
        artist_name = results[0].get("name")
        for album in albums:
            album.setdefault("artist_id", id)
            album.setdefault("artist_name", artist_name)
        page = albums[offset : offset + limit]
        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=len(albums),
            items=self._albums_to_browse_items(page),
        )

    async def _browse_playlist(
        self, id: str, offset: int, limit: int
    ) -> BrowseItemList:
        results = await self.client.request(
            "playlists/tracks",
            {"id": id, "audioformat": self.audio_format},
        )
        if not results:
            return EmptyList(offset, limit)
        tracks = results[0].get("tracks", [])
        page = tracks[offset : offset + limit]
        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=len(tracks),
            items=self._tracks_to_browse_items(page),
        )

    async def _browse_catalog(
        self, endpoint: str, offset: int, limit: int
    ) -> BrowseItemList:
        if endpoint == "root":
            return self._root_catalog(offset, limit)
        elif endpoint == "popular-tracks":
            results = await self.client.request(
                "tracks",
                {
                    "order": "popularity_month",
                    "offset": offset,
                    "limit": limit,
                    "audioformat": self.audio_format,
                },
            )
            items = self._tracks_to_browse_items(results)
        elif endpoint == "popular-albums":
            results = await self.client.request(
                "albums",
                {"order": "popularity_month", "offset": offset, "limit": limit},
            )
            items = self._albums_to_browse_items(results)
        elif endpoint == "new-releases":
            results = await self.client.request(
                "albums",
                {"order": "releasedate_desc", "offset": offset, "limit": limit},
            )
            items = self._albums_to_browse_items(results)
        elif endpoint == "popular-artists":
            results = await self.client.request(
                "artists",
                {"order": "popularity_total", "offset": offset, "limit": limit},
            )
            items = self._artists_to_browse_items(results)
        elif endpoint == "featured-playlists":
            results = await self.client.request(
                "playlists",
                {"order": "creationdate_desc", "offset": offset, "limit": limit},
            )
            items = self._playlists_to_browse_items(results)
        else:
            return EmptyList(offset, limit)

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=_estimated_total(offset, limit, len(items)),
            items=items,
        )

    def _root_catalog(self, offset: int, limit: int) -> BrowseItemList:
        shelves = [
            (
                "popular-tracks",
                "Popular Tracks",
                PreviewType.TILE,
                PreviewContentType.TRACK,
                CatalogRole.DISCOVERY,
            ),
            (
                "new-releases",
                "New Releases",
                PreviewType.IMAGE_TEXT,
                PreviewContentType.ALBUM,
                CatalogRole.DISCOVERY,
            ),
            (
                "popular-albums",
                "Popular Albums",
                PreviewType.IMAGE_TEXT,
                PreviewContentType.ALBUM,
                CatalogRole.DISCOVERY,
            ),
            (
                "popular-artists",
                "Popular Artists",
                PreviewType.IMAGE_TEXT,
                PreviewContentType.ARTIST,
                CatalogRole.DISCOVERY,
            ),
            (
                "featured-playlists",
                "Featured Playlists",
                PreviewType.IMAGE_TEXT,
                PreviewContentType.PLAYLIST,
                CatalogRole.HIDE_ON_HOME,
            ),
        ]
        all_items = [
            BrowseItem(
                id=catalog_id(slug),
                name=title,
                can_browse=True,
                can_add=False,
                catalog=Catalog(
                    id=catalog_id(slug),
                    title=title,
                    can_genre_filter=False,
                    preview_config=Preview(
                        type=ptype,
                        content_type=ctype,
                        items_count=20,
                        rows_count=2,
                        aspect_ratio=1.0,
                    ),
                    role=role,
                ),
            )
            for slug, title, ptype, ctype, role in shelves
        ]
        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=len(all_items),
            items=all_items[offset : offset + limit],
        )

    # ------------------------------------------------------------------
    # Track playback
    # ------------------------------------------------------------------

    async def get_track_info(self, track_ids: List[str]) -> List[TrackInfo]:
        if not track_ids:
            return []

        # Jamendo accepts multiple ids in a single call, space-separated.
        all_tracks: dict[str, dict] = {}
        chunk_size = MAX_LIMIT
        for i in range(0, len(track_ids), chunk_size):
            chunk = track_ids[i : i + chunk_size]
            results = await self.client.request(
                "tracks",
                {
                    "id": " ".join(chunk),
                    "limit": len(chunk),
                    "audioformat": self.audio_format,
                },
            )
            for track in results:
                all_tracks[str(track["id"])] = track

        # Preserve the requested order; drop ids Jamendo didn't return.
        return [
            self._track_to_track_info(all_tracks[tid])
            for tid in track_ids
            if tid in all_tracks
        ]

    def _track_to_track_info(self, track) -> TrackInfo:
        audio_url = track.get("audio", "")
        mime = self.audio_mime

        async def link_retriever() -> TrackUrl:
            return TrackUrl(url=audio_url, format=mime)

        return TrackInfo(
            id=track_id(str(track["id"])),
            link_retriever=link_retriever,
            metadata=self._track_metadata(track),
        )

    # ------------------------------------------------------------------
    # get(entity_id)
    # ------------------------------------------------------------------

    async def get(self, entity_id: EntityId) -> BrowseItem:
        if entity_id.type == EntityType.TRACK:
            results = await self.client.request(
                "tracks",
                {"id": entity_id.id, "audioformat": self.audio_format},
            )
            items = self._tracks_to_browse_items(results)
        elif entity_id.type == EntityType.ALBUM:
            results = await self.client.request("albums", {"id": entity_id.id})
            items = self._albums_to_browse_items(results)
        elif entity_id.type == EntityType.ARTIST:
            results = await self.client.request("artists", {"id": entity_id.id})
            items = self._artists_to_browse_items(results)
        elif entity_id.type == EntityType.PLAYLIST:
            results = await self.client.request("playlists", {"id": entity_id.id})
            items = self._playlists_to_browse_items(results)
        else:
            raise ValueError(f"Unsupported EntityId type: {entity_id.type.name}")

        if not items:
            raise ValueError(f"Entity not found: {entity_id.to_string}")
        return items[0]

    # ------------------------------------------------------------------
    # Mapping helpers
    # ------------------------------------------------------------------

    def _track_metadata(self, track, album_meta: Optional[dict] = None) -> Track:
        album_meta = album_meta or {}
        aid = str(track.get("album_id") or album_meta.get("id") or "")
        album_name = track.get("album_name") or album_meta.get("name") or ""
        image = (
            track.get("album_image")
            or track.get("image")
            or album_meta.get("image")
        )
        performer = Artist(
            id=artist_id(str(track.get("artist_id", ""))),
            name=track.get("artist_name", ""),
        )
        return Track(
            id=track_id(str(track["id"])),
            title=track.get("name", ""),
            duration=int(track.get("duration", 0) or 0),
            performer=performer,
            album=Album(
                id=album_id(aid),
                title=album_name,
                artist=performer,
                image=_cover(image),
            ),
        )

    def _tracks_to_browse_items(
        self, tracks, album_meta: Optional[dict] = None
    ) -> List[BrowseItem]:
        result = []
        for track in tracks:
            meta = self._track_metadata(track, album_meta)
            result.append(
                BrowseItem(
                    id=meta.id,
                    name=meta.title,
                    subname=meta.performer.name if meta.performer else None,
                    can_browse=False,
                    can_add=True,
                    track=meta,
                )
            )
        return result

    def _albums_to_browse_items(self, albums) -> List[BrowseItem]:
        result = []
        for album in albums:
            aid = str(album["id"])
            artist = Artist(
                id=artist_id(str(album.get("artist_id", ""))),
                name=album.get("artist_name", ""),
            )
            result.append(
                BrowseItem(
                    id=album_id(aid),
                    name=album.get("name", ""),
                    subname=artist.name,
                    can_browse=True,
                    can_add=True,
                    album=Album(
                        id=album_id(aid),
                        title=album.get("name", ""),
                        artist=artist,
                        image=_cover(album.get("image")),
                    ),
                    sections=[_album_tracks_section(aid)],
                )
            )
        return result

    def _artists_to_browse_items(self, artists) -> List[BrowseItem]:
        result = []
        for artist in artists:
            aid = str(artist["id"])
            result.append(
                BrowseItem(
                    id=artist_id(aid),
                    name=artist.get("name", ""),
                    subname=None,
                    can_browse=True,
                    can_add=False,
                    artist=Artist(
                        id=artist_id(aid),
                        name=artist.get("name", ""),
                        image=_cover(artist.get("image")),
                    ),
                    sections=[_artist_albums_section(aid)],
                )
            )
        return result

    def _playlists_to_browse_items(self, playlists) -> List[BrowseItem]:
        result = []
        for playlist in playlists:
            pid = str(playlist["id"])
            tracks = playlist.get("tracks", [])
            owner = Owner(
                name=playlist.get("user_name", ""),
                id=user_id(str(playlist.get("user_id", ""))),
            )
            result.append(
                BrowseItem(
                    id=playlist_id(pid),
                    name=playlist.get("name", ""),
                    subname=owner.name,
                    can_browse=True,
                    can_add=True,
                    playlist=Playlist(
                        id=playlist_id(pid),
                        name=playlist.get("name", ""),
                        owner=owner,
                        description=None,
                        track_count=len(tracks),
                    ),
                    sections=[_playlist_tracks_section(pid)],
                )
            )
        return result

    # ------------------------------------------------------------------
    # Unsupported capabilities — favourites, playlist mutation, genres.
    # These need an OAuth2 user session (favourites/playlists) or have no
    # Jamendo endpoint (genre list), so they degrade gracefully.
    # ai_search is intentionally not overridden; the Protocol default returns
    # an empty list until the semantic backend is wired up.
    # ------------------------------------------------------------------

    async def list_favorite(
        self, type: SearchType, filter: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        return EmptyList(offset, limit)

    async def get_favorite_ids(self) -> FavoriteIds:
        return FavoriteIds()

    async def add_to_favorite(self, id: str):
        logger.warning("Favorites are not supported in the Jamendo input module")

    async def remove_from_favorite(self, id: str):
        logger.warning("Favorites are not supported in the Jamendo input module")

    async def list_genre(self, offset: int = 0, limit: int = 25) -> GenreList:
        # Jamendo has no genre taxonomy — it uses free-form tags.
        return GenreList(offset=offset, limit=limit, total=0, items=[])

    async def playlist_user_list(
        self, offset: int = 0, limit: int = 25
    ) -> BrowseItemList:
        return EmptyList(offset, limit)

    async def playlist_create(self, name: str, description: str) -> Playlist:
        raise NotImplementedError(
            "Playlist management is not supported in the Jamendo input module"
        )

    async def playlist_update(
        self, id: str, name: Optional[str], description: Optional[str]
    ) -> Playlist:
        raise NotImplementedError(
            "Playlist management is not supported in the Jamendo input module"
        )

    async def playlist_delete(self, id: str):
        raise NotImplementedError(
            "Playlist management is not supported in the Jamendo input module"
        )

    async def playlist_add_tracks(
        self, id: str, track_ids: List[str], allow_duplicates: bool
    ) -> Playlist:
        raise NotImplementedError(
            "Playlist management is not supported in the Jamendo input module"
        )

    async def playlist_remove_tracks(
        self, id: str, playlist_track_ids: List[str]
    ) -> Playlist:
        raise NotImplementedError(
            "Playlist management is not supported in the Jamendo input module"
        )

    async def get_resource_path(self, id: str) -> str | None:
        return None
