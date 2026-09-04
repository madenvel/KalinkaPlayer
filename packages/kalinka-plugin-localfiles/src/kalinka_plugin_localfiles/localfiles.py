import asyncio
import logging
import multiprocessing
import os
import time
from pathlib import Path
from typing import List, Dict, Optional
import mimetypes

from fastapi import HTTPException
from .config_model import LocalFilesConfig
from kalinka_plugin_sdk.inputmodule import (
    ContentInfo,
    InputModule,
    ModuleAsset,
    SearchType,
    SourceUnavailableError,
    TrackInfo,
    TrackSource,
)
from kalinka_plugin_sdk.datamodel import (
    BrowseItem,
    BrowseItemList,
    EntityId,
    EntityType,
    PreviewContentType,
    Track,
    Album,
    CoverImage,
    Artist,
    Preview,
    PreviewType,
    CardSize,
    EmptyList,
    Playlist,
    Catalog,
    CatalogRole,
    FavoriteIds,
    Genre,
    GenreList,
    Owner,
)
from .utils.id_generator import generate_playlist_id
from .utils.image_utils import create_playlist_cover_collage
from .utils.mount_status import await_root_available, root_of
from .utils.name_utils import expand_music_folders, path_within_roots
from .input_module_db import LocalFilesInputModuleDb

logger = logging.getLogger(__name__.split(".")[-1])

# A hard NFS mount can otherwise block a stat indefinitely.
STAT_TIMEOUT_S = 5.0


def artist_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.ARTIST, source="localfiles")


def album_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.ALBUM, source="localfiles")


def track_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.TRACK, source="localfiles")


def playlist_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.PLAYLIST, source="localfiles")


def label_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.LABEL, source="localfiles")


def genre_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.GENRE, source="localfiles")


def user_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.USER, source="localfiles")


def catalog_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.CATALOG, source="localfiles")


def artist_display_name(name: Optional[str]) -> str:
    """A row the enricher has not reached yet may have no artist name;
    browse output still needs one."""
    return name or "Unknown artist"


class LocalFilesInputModule(InputModule):
    """Local music files input module implementation"""

    def __init__(
        self,
        config: LocalFilesConfig,
        db_manager: LocalFilesInputModuleDb,
        search_request_queue: Optional[multiprocessing.Queue] = None,
        search_response_queue: Optional[multiprocessing.Queue] = None,
    ):
        # Use the specialized LocalFilesInputModuleDb passed from module_setup.py
        self.config = config
        self.db_manager = db_manager
        self.artwork_path = Path(config.artwork_path).expanduser().resolve()
        # Access boundary: only files under a configured music folder may be
        # served / played. Captured once here, so it is fixed for the lifetime
        # of this module instance — a live config edit via PUT /server/config
        # does not re-run setup(), so the new boundary only takes effect on the
        # next restart / re-setup (at which point the indexer also purges the
        # now-out-of-scope rows).
        self._music_folders = expand_music_folders(config.music_folders)
        self._search_request_queue = search_request_queue
        self._search_response_queue = search_response_queue
        self._search_lock = asyncio.Lock()

        # Ensure artwork directories exist
        os.makedirs(self.artwork_path / "album", exist_ok=True)
        os.makedirs(self.artwork_path / "artist", exist_ok=True)
        os.makedirs(self.artwork_path / "playlist", exist_ok=True)

        # Initialize mime types for serving files
        mimetypes.init()
        mimetypes.add_type("audio/flac", ".flac")

    def module_name(self) -> str:
        """Return the name of the module"""
        return "localfiles"

    def display_name(self) -> str:
        """Human-friendly source name for section headers."""
        return "Your Library"

    async def ai_search(
        self, query: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        """Semantic search via the searcher subprocess (CLAP KNN + mood + tags).

        Returns a single AI-suggestions catalog card ("FROM YOUR LIBRARY") of
        semantically ranked tracks — the plugin owns this card's
        presentation. BEST MATCH (literal name lookup) is assembled by the
        server, which appends this card after it, alongside the other sources'
        cards. The server may suppress the card for a navigational query.
        """
        if self._search_request_queue is None or self._search_response_queue is None:
            return EmptyList(offset, limit)

        ai_cfg = self.config.ai_search

        logger.info(
            "ai_search: sending query %r (limit=%d)", query, ai_cfg.max_results
        )
        async with self._search_lock:
            self._search_request_queue.put(
                {"query": query, "limit": ai_cfg.max_results}
            )
            try:
                ids: dict = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._search_response_queue.get(timeout=30),
                )
            except Exception:
                logger.warning("ai_search: timed out waiting for searcher response")
                return EmptyList(offset, limit)

        track_ids = ids.get("tracks", [])
        logger.info("ai_search: response — %d tracks", len(track_ids))
        if not track_ids:
            return EmptyList(offset, limit)

        # Preserve the searcher's rank order (get_tracks_by_ids does not).
        by_id = {t["id"]: t for t in self.db_manager.get_tracks_by_ids(track_ids)}
        tracks = [
            self._create_track_browse_item(by_id[tid])
            for tid in track_ids
            if tid in by_id
        ]
        if not tracks:
            return EmptyList(offset, limit)

        cat = catalog_id("ai_search:tracks")
        card = BrowseItem(
            id=cat,
            name="FROM YOUR LIBRARY",
            subname="Matched by mood, genre and audio features",
            can_browse=False,
            can_add=False,
            catalog=Catalog(
                id=cat,
                title="FROM YOUR LIBRARY",
                sources=[cat.source],
                preview_config=Preview(
                    type=PreviewType.CARD,
                    content_type=PreviewContentType.TRACK,
                    icon="ai_suggestions",
                    items_count=len(tracks),
                ),
            ),
            sections=tracks,
        )
        return BrowseItemList(offset=offset, limit=limit, total=1, items=[card])

    _SEARCH_HANDLERS = {
        SearchType.track: "_search_tracks",
        SearchType.album: "_search_albums",
        SearchType.artist: "_search_artists",
        SearchType.playlist: "_search_playlists",
    }

    async def search(
        self, type: SearchType, query: str, offset=0, limit=50
    ) -> BrowseItemList:
        """Search the local database for *type* matching *query*.

        The query path is synchronous sqlite — a full-table ``LIKE`` scan with
        a per-row ``fold()`` callback — so it runs in a thread executor instead
        of on the event loop. Otherwise it blocks the loop and the other
        sources' external (Qobuz/Jamendo) requests can't fire until the local
        scan finishes (the assembler fans all sources out concurrently).
        """
        handler = self._SEARCH_HANDLERS.get(type)
        if handler is None:
            logger.warning("Unsupported search type: %s", type)
            return EmptyList(offset, limit)
        return await asyncio.get_running_loop().run_in_executor(
            None, getattr(self, handler), query, offset, limit
        )

    def _search_tracks(self, query: str, offset: int, limit: int) -> BrowseItemList:
        """Search for tracks"""
        tracks, total = self.db_manager.search_tracks(query, offset, limit)

        items = []
        for track in tracks:
            items.append(self._create_track_browse_item(track))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def _search_albums(self, query: str, offset: int, limit: int) -> BrowseItemList:
        """Search for albums"""
        albums, total = self.db_manager.search_albums(query, offset, limit)

        items = []
        for album in albums:
            items.append(self._create_album_browse_item(album))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def _search_artists(self, query: str, offset: int, limit: int) -> BrowseItemList:
        """Search for artists"""
        artists, total = self.db_manager.search_artists(query, offset, limit)

        items = []
        for artist in artists:
            items.append(self._create_artist_browse_item(artist))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def _search_playlists(self, query: str, offset: int, limit: int) -> BrowseItemList:
        """Search for playlists"""
        playlists, total = self.db_manager.search_playlists(query, offset, limit)

        items = []
        for playlist in playlists:
            items.append(self._create_playlist_browse_item(playlist))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    async def browse(
        self,
        entity_id: EntityId,
        offset: int = 0,
        limit: int = 50,
        genre_ids: List[EntityId] = [],
    ) -> BrowseItemList:
        """Browse items based on the entity ID"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return EmptyList(offset, limit)

        if entity_id.type == EntityType.ALBUM:
            return self._browse_album(entity_id.id, offset, limit)
        elif entity_id.type == EntityType.ARTIST:
            return self._browse_artist(entity_id.id, offset, limit)
        elif entity_id.type == EntityType.PLAYLIST:
            return self._browse_playlist(entity_id.id, offset, limit)
        elif entity_id.type == EntityType.CATALOG:
            return await self.browse_catalog(
                entity_id.id, offset=offset, limit=limit, genre_ids=genre_ids
            )

        return EmptyList(offset, limit)

    async def browse_catalog(
        self,
        endpoint: str,
        offset: int = 0,
        limit: int = 50,
        genre_ids: List[EntityId] = [],
    ) -> BrowseItemList:
        """Browse the catalog endpoints"""
        if endpoint == "root":
            return self._browse_root(offset, limit)
        elif endpoint == "recent":
            return self._browse_recently_added(offset, limit)
        elif endpoint == "albums":
            return self._browse_albums(offset, limit)
        elif endpoint == "artists":
            return self._browse_artists(offset, limit)
        elif endpoint == "playlists":
            return self._browse_playlists(offset, limit)
        else:
            ep = endpoint.split("-")
            if len(ep) == 2 and ep[0] == "tracks":
                return self._browse_artist_tracks(ep[1], offset, limit)
            else:
                logger.warning(f"Unknown catalog endpoint: {endpoint}")
                return EmptyList(offset, limit)

    def _browse_root(self, offset: int, limit: int) -> BrowseItemList:
        """Return the root catalog with main sections"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return EmptyList(0, 0)

        recent_tracks, recent_total = self.db_manager.get_recently_added_tracks(0, 10)
        albums, albums_total = self.db_manager.get_all_albums(0, 10)
        artists, artists_total = self.db_manager.get_all_artists(0, 10)
        playlists, playlists_total = self.db_manager.get_all_playlists(0, 10)

        # Create the main sections
        items = []

        # Recently Added section
        if recent_total > 0:
            preview = Preview(
                type=PreviewType.TILE,
                content_type=PreviewContentType.TRACK,
                icon="recent",
                items_count=10,  # Fixed value: maximum number of items to display in preview
                rows_count=1,
                card_size=CardSize.SMALL,
            )

            catalog = Catalog(
                id=catalog_id("recent"),
                title="Recently Added",
                can_genre_filter=False,
                description="Recently added tracks",
                preview_config=preview,
                role=CatalogRole.LIBRARY,
            )

            recent_section = BrowseItem(
                id=catalog_id("recent"),
                name="Recently Added",
                can_browse=True,
                can_add=False,
                catalog=catalog,
                subname=f"{recent_total} tracks",
            )

            items.append(recent_section)

        # Albums section
        if albums_total > 0:
            preview = Preview(
                type=PreviewType.IMAGE_TEXT,
                content_type=PreviewContentType.ALBUM,
                icon="album",
                items_count=10,  # Fixed value: maximum number of items to display in preview
                rows_count=1,
                card_size=CardSize.SMALL,
            )

            catalog = Catalog(
                id=catalog_id("albums"),
                title="My Albums",
                can_genre_filter=False,
                description="Browse your album collection",
                preview_config=preview,
                role=CatalogRole.LIBRARY,
            )

            album_section = BrowseItem(
                id=catalog_id("albums"),
                name="My Albums",
                can_browse=True,
                can_add=False,
                catalog=catalog,
                subname=f"{albums_total} albums",
            )

            items.append(album_section)

        # Artists section
        if artists_total > 0:
            preview = Preview(
                type=PreviewType.IMAGE_TEXT,
                content_type=PreviewContentType.ARTIST,
                icon="artist",
                items_count=10,  # Fixed value: maximum number of items to display in preview
                rows_count=1,
                card_size=CardSize.SMALL,
            )

            catalog = Catalog(
                id=catalog_id("artists"),
                title="My Artists",
                can_genre_filter=False,
                description="Browse your artist collection",
                preview_config=preview,
                role=CatalogRole.LIBRARY,
            )

            artist_section = BrowseItem(
                id=catalog_id("artists"),
                name="My Artists",
                can_browse=True,
                can_add=False,
                catalog=catalog,
                subname=f"{artists_total} artists",
            )

            items.append(artist_section)

        # Playlists section
        if playlists_total > 0:
            preview = Preview(
                type=PreviewType.IMAGE_TEXT,
                content_type=PreviewContentType.PLAYLIST,
                icon="playlist",
                items_count=10,
                rows_count=1,
                card_size=CardSize.SMALL,
            )

            catalog = Catalog(
                id=catalog_id("playlists"),
                title="My Playlists",
                can_genre_filter=False,
                description="Browse your playlists",
                preview_config=preview,
                role=CatalogRole.LIBRARY,
            )

            playlist_section = BrowseItem(
                id=catalog_id("playlists"),
                name="My Playlists",
                can_browse=True,
                can_add=False,
                catalog=catalog,
                subname=f"{playlists_total} playlists",
            )

            items.append(playlist_section)

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=len(items),
            items=items[offset : offset + limit],
        )

    def _browse_recently_added(self, offset: int, limit: int) -> BrowseItemList:
        """Browse recently added tracks"""
        tracks, total = self.db_manager.get_recently_added_tracks(offset, limit)

        items = []
        for track in tracks:
            items.append(self._create_track_browse_item(track))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def _browse_albums(self, offset: int, limit: int) -> BrowseItemList:
        """Browse all albums"""
        albums, total = self.db_manager.get_all_albums(offset, limit)

        items = []
        for album in albums:
            items.append(self._create_album_browse_item(album))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def _browse_artists(self, offset: int, limit: int) -> BrowseItemList:
        """Browse all artists"""
        artists, total = self.db_manager.get_all_artists(offset, limit)

        items = []
        for artist in artists:
            items.append(self._create_artist_browse_item(artist))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def _browse_artist_tracks(
        self, artist_id: str, offset: int, limit: int
    ) -> BrowseItemList:
        """Browse tracks by an artist"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return EmptyList(0, 0)

        tracks, total = self.db_manager.get_artist_recent_tracks(
            artist_id, offset, limit
        )

        items = []
        for track in tracks:
            items.append(self._create_track_browse_item(track))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def _browse_playlists(self, offset: int, limit: int) -> BrowseItemList:
        """Browse all playlists"""
        playlists, total = self.db_manager.get_all_playlists(offset, limit)

        items = []
        for playlist in playlists:
            items.append(self._create_playlist_browse_item(playlist))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def _browse_album(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        """Browse tracks in an album"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return EmptyList(0, 0)

        tracks, total = self.db_manager.get_album_tracks(id, offset, limit)

        items = []
        for track in tracks:
            items.append(self._create_track_browse_item(track))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def _browse_artist(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        """Browse an artist's albums followed by their orphan tracks.

        Tracks count as "orphan" for this view when their album is
        anchored to a different artist — either ``unknown_album``
        (enricher couldn't pick a release) or a V/A compilation
        anchored to ``various_artists`` (e.g. a Jamendo playlist
        folder). Without surfacing them, an artist whose only local
        contributions are on a V/A compilation would appear empty in
        the UI even though their tracks exist. We append them after
        the real albums; pagination spans both collections.
        """
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return EmptyList(0, 0)

        # Fetch albums first; albums_total tells us where the window crosses
        # into the orphan-tracks section.
        albums, albums_total = self.db_manager.get_artist_albums(id, offset, limit)

        items: List[BrowseItem] = [
            self._create_album_browse_item(album) for album in albums
        ]

        # If the window extends past the albums, fill the remainder with
        # orphan tracks at offset (offset - albums_total).
        remaining = limit - len(items)
        track_offset = max(0, offset - albums_total)
        orphans_total = 0
        if remaining > 0:
            tracks, orphans_total = self.db_manager.get_artist_orphan_tracks(
                id, track_offset, remaining
            )
            for track in tracks:
                items.append(self._create_track_browse_item(track))
        else:
            # Still need orphans_total for the overall total.
            _, orphans_total = self.db_manager.get_artist_orphan_tracks(id, 0, 0)

        total = albums_total + orphans_total
        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def _browse_playlist(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        """Browse tracks in a playlist"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return EmptyList(0, 0)

        tracks, total = self.db_manager.get_playlist_tracks(id, offset, limit)

        items = []
        for track in tracks:
            items.append(self._create_track_browse_item(track))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    async def get_track_info(self, track_ids: List[str]) -> List[TrackInfo]:
        """Get track info for a list of track IDs"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return []

        tracks = self.db_manager.get_tracks_by_ids(track_ids)

        # Create a dictionary of tracks indexed by ID for quick lookup
        track_dict = {track["id"]: track for track in tracks}

        # Maintain the same order as track_ids
        result = []
        for track_id_str in track_ids:
            if track_id_str in track_dict:
                track = track_dict[track_id_str]
                track_metadata = self._create_track_metadata(track)

                # Validating at play time guards the window between a
                # folder-config change and the next index scan: raising here
                # surfaces the track as unavailable instead of queueing a
                # dead source.
                def create_source_retriever(track_db_id, track_path, track_format):
                    async def source_retriever():
                        await self._await_readable(track_path)
                        return TrackSource(
                            source=ModuleAsset(
                                module=self.module_name(), asset_id=track_db_id
                            ),
                            format=track_format,
                        )

                    return source_retriever

                result.append(
                    TrackInfo(
                        id=track_id(track["id"]),
                        source_retriever=create_source_retriever(
                            track["id"], track["file_path"], track["format"]
                        ),
                        metadata=track_metadata,
                    )
                )

        return result

    def _require_readable(self, track_path: str) -> None:
        """Raise unless the file may still be served: inside a configured music
        folder, present, and readable."""
        if not path_within_roots(track_path, self._music_folders):
            raise PermissionError(
                f"Track path is outside the configured music folders: {track_path}"
            )
        if not os.path.exists(track_path):
            raise FileNotFoundError(f"Track file no longer exists: {track_path}")
        if not os.access(track_path, os.R_OK):
            raise PermissionError(f"Track file is not readable: {track_path}")

    async def _require_readable_bounded(self, track_path: str) -> None:
        """`_require_readable` off the event loop, bounded: a hung network
        mount reads as transiently unavailable instead of pinning the
        request."""
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._require_readable, track_path),
                STAT_TIMEOUT_S,
            )
        except (asyncio.TimeoutError, TimeoutError):
            raise SourceUnavailableError(
                "Music storage did not respond (hung network mount?)"
            )

    async def _await_readable(self, track_path: str) -> None:
        """`_require_readable` with a mount-aware second chance: when the file
        is missing because its music folder is an unmounted share — offline,
        automount pending, or a static mount whose recorded identity no
        longer matches — raise SourceUnavailableError instead of declaring
        the track gone."""
        try:
            await self._require_readable_bounded(track_path)
            return
        except FileNotFoundError:
            root = root_of(track_path, self._music_folders)
            if root is None:
                raise
        status = await await_root_available(root)
        if not status.available:
            raise SourceUnavailableError(
                f"Music folder {root} is not available: {status.reason}"
            )
        stored = self.db_manager.get_root_signature(root)
        if stored and status.identity and status.identity != stored:
            raise SourceUnavailableError(
                f"Music folder {root} is not mounted "
                f"(the library was indexed from {stored})"
            )
        await self._require_readable_bounded(track_path)

    async def get_content_info(self, asset_id: str) -> Optional[ContentInfo]:
        """Resolve a track id to the file the server serves for it.

        Re-runs the access check the source retriever made, because a folder
        can leave the configuration between the two. A file outside the
        boundary is reported as absent, never as forbidden.
        """
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return None

        track = self.db_manager.get_track_by_id(asset_id)
        track_path = (track or {}).get("file_path")
        if not track_path:
            return None
        try:
            await self._await_readable(track_path)
        except OSError as e:
            logger.warning("Refusing content for track %s: %s", asset_id, e)
            return None

        return ContentInfo(
            mime_type=mimetypes.guess_type(track_path)[0]
            or "application/octet-stream",
            local_path=track_path,
            cacheable=True,
        )

    async def list_favorite(
        self, type: SearchType, filter: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        """List favorites - for playlists, returns all user playlists"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return EmptyList(0, 0)

        if type == SearchType.playlist:
            # Return all user playlists as favorites
            playlists, total = self.db_manager.get_all_playlists(
                offset, limit, filter_text=filter
            )

            items = []
            for playlist in playlists:
                items.append(self._create_playlist_browse_item(playlist))

            return BrowseItemList(offset=offset, limit=limit, total=total, items=items)
        else:
            # For other types, return an empty list as before
            return EmptyList(offset, limit)

    async def get_favorite_ids(self) -> FavoriteIds:
        """Get favorite IDs (not supported)"""
        return FavoriteIds(tracks=[], albums=[], artists=[], playlists=[])

    async def add_to_favorite(self, id: str):
        """Add to favorites (not supported)"""
        logger.warning("Favorites are not supported in local files input module")

    async def remove_from_favorite(self, id: str):
        """Remove from favorites (not supported)"""
        logger.warning("Favorites are not supported in local files input module")

    async def list_genre(self, offset: int = 0, limit: int = 25) -> GenreList:
        """List genres (not implemented yet)"""
        return GenreList(total=0, offset=offset, limit=limit, items=[])

    async def get(self, entity_id: EntityId) -> BrowseItem:
        """Get details for a specific entity by ID"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        if entity_id.type == EntityType.ALBUM:
            return self._album_get(entity_id.id)
        elif entity_id.type == EntityType.ARTIST:
            return self._artist_get(entity_id.id)
        elif entity_id.type == EntityType.TRACK:
            return self._track_get(entity_id.id)
        elif entity_id.type == EntityType.PLAYLIST:
            return self._playlist_get(entity_id.id)
        else:
            logger.warning(f"Unsupported entity type: {entity_id.type}")
            raise HTTPException(status_code=404, detail="Entity not found")

    def _album_get(self, id: str) -> BrowseItem:
        """Get album details"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        album = self.db_manager.get_album_by_id(id)
        if not album:
            logger.warning(f"Album not found: {id}")
            raise HTTPException(status_code=404, detail=f"Album not found: {id}")

        return self._create_album_browse_item(album)

    def _artist_get(self, id: str) -> BrowseItem:
        """Get artist details"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        artist = self.db_manager.get_artist_by_id(id)
        if not artist:
            logger.warning(f"Artist not found: {id}")
            raise HTTPException(status_code=404, detail=f"Artist not found: {id}")

        return self._create_artist_browse_item(artist)

    def _track_get(self, id: str) -> BrowseItem:
        """Get track details"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        track = self.db_manager.get_track_by_id(id)
        if not track:
            logger.warning(f"Track not found: {id}")
            raise HTTPException(status_code=404, detail=f"Track not found: {id}")

        return self._create_track_browse_item(track)

    def _playlist_get(self, id: str) -> BrowseItem:
        """Get playlist details"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        playlist = self.db_manager.get_playlist_by_id(id)
        if not playlist:
            logger.warning(f"Playlist not found: {id}")
            raise HTTPException(status_code=404, detail=f"Playlist not found: {id}")

        return self._create_playlist_browse_item(playlist)

    async def playlist_user_list(
        self, offset: int = 0, limit: int = 25
    ) -> BrowseItemList:
        """List user playlists"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return EmptyList(0, 0)

        playlists, total = self.db_manager.get_all_playlists(offset, limit)

        items = []
        for playlist in playlists:
            items.append(self._create_playlist_browse_item(playlist))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    async def playlist_create(self, name: str, description: str) -> Playlist:
        """Create playlist"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")
        # Generate playlist ID using the name and system as creator
        playlist_id_str = generate_playlist_id(name, "localfiles_system")
        # Create the playlist record
        self.db_manager.create_playlist(
            playlist_id_str, name, description, "localfiles_system"
        )

        # Get the created playlist
        playlist = self.db_manager.get_playlist_by_id(playlist_id_str)
        if not playlist:
            logger.error(f"Failed to retrieve created playlist: {playlist_id_str}")
            raise HTTPException(status_code=500, detail="Failed to create playlist")

        owner = Owner(name="Local System", id=user_id("localfiles_system"))

        return Playlist(
            id=playlist_id(playlist_id_str),
            name=name,
            description=description,
            track_count=playlist.get("track_count", 0),
            # last_updated=playlist["last_updated"],
            owner=owner,
        )

    async def playlist_update(
        self, id: str, name: Optional[str], description: Optional[str]
    ) -> Playlist:
        """Update playlist"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        id = EntityId.from_string(id).id

        self.db_manager.update_playlist(id, name, description)
        playlist = self.db_manager.get_playlist_by_id(id)

        if not playlist:
            logger.warning(f"Playlist not found: {id}")
            raise HTTPException(status_code=404, detail=f"Playlist not found: {id}")

        owner = Owner(name="Local System", id=user_id("localfiles_system"))

        playlist_obj = Playlist(
            id=playlist_id(playlist["id"]),
            name=playlist["name"],
            description=playlist["description"],
            track_count=playlist.get("track_count", 0),
            # last_updated=playlist["last_updated"],
            owner=owner,
        )

        # Add image if available
        image_path = self._get_playlist_image_urls(playlist["id"])
        if image_path:
            playlist_obj.image = image_path

        return playlist_obj

    async def playlist_delete(self, id: str):
        """Delete playlist"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return

        id = EntityId.from_string(id).id

        self.db_manager.delete_playlist(id)

    async def playlist_add_tracks(
        self, id: str, track_ids: List[str], allow_duplicates: bool = False
    ) -> Playlist:
        """Add tracks to playlist"""

        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        id = EntityId.from_string(id).id

        # Add tracks to the playlist
        tracks_added = self.db_manager.add_tracks_to_playlist(
            id,
            [EntityId.from_string(track_id).id for track_id in track_ids],
            allow_duplicates,
        )

        # Generate playlist cover image if tracks were added
        if tracks_added > 0:
            image_url = self._generate_playlist_cover(id)
            if image_url:
                self.db_manager.update_playlist_image(id, image_url)

        # Get updated playlist
        playlist = self.db_manager.get_playlist_by_id(id)

        if not playlist:
            logger.warning(f"Playlist not found: {id}")
            raise HTTPException(status_code=404, detail=f"Playlist not found: {id}")

        owner = Owner(name="Local System", id=user_id("localfiles_system"))

        # Create Playlist object
        playlist_obj = Playlist(
            id=playlist_id(playlist["id"]),
            name=playlist["name"],
            description=playlist["description"],
            track_count=playlist.get("track_count", 0),
            # duration=playlist.get("duration", 0),
            # last_updated=playlist["last_updated"],
            owner=owner,
        )

        # Add image if available
        image_path = self._get_playlist_image_urls(playlist["id"])
        if image_path:
            playlist_obj.image = image_path

        return playlist_obj

    def _generate_playlist_cover(self, playlist_id: str) -> Optional[str]:
        """
        Generate a cover image for a playlist based on its tracks.

        For playlists with tracks from 4 or more different albums, creates a 2x2 collage.
        For playlists with fewer unique albums, copies the album cover of the first track.

        Args:
            playlist_id: The ID of the playlist

        Returns:
            True if the cover was generated successfully, False otherwise
        """
        # Get up to 4 distinct album IDs from the playlist
        album_ids = self.db_manager.get_playlist_track_album_ids(playlist_id, limit=4)

        if not album_ids:
            logger.warning(
                f"No tracks in playlist {playlist_id} to generate cover image"
            )
            return None

        # Create the cover image
        return create_playlist_cover_collage(album_ids, self.artwork_path, playlist_id)

    async def playlist_remove_tracks(
        self, id: str, playlist_track_ids: List[str]
    ) -> Playlist:
        """Remove tracks from playlist"""

        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        self.db_manager.remove_tracks_from_playlist(id, playlist_track_ids)
        playlist = self.db_manager.get_playlist_by_id(id)

        if not playlist:
            logger.warning(f"Playlist not found: {id}")
            raise HTTPException(status_code=404, detail=f"Playlist not found: {id}")

        owner = Owner(name="Local System", id=user_id("localfiles_system"))

        playlist_obj = Playlist(
            id=playlist_id(playlist["id"]),
            name=playlist["name"],
            description=playlist["description"],
            track_count=playlist.get("track_count", 0),
            # duration=playlist.get("duration", 0),
            # last_updated=playlist["last_updated"],
            owner=owner,
        )

        # Add image if available
        image_path = self._get_playlist_image_urls(playlist["id"])
        if image_path:
            playlist_obj.image = image_path

        return playlist_obj

    def _create_track_metadata(self, track: Dict) -> Track:
        """Create a Track object from database data"""
        # Create album object
        album = Album(
            id=album_id(track["album_id"]),
            title=track["album_title"],
            artist=Artist(
                id=artist_id(track["artist_id"]),
                name=artist_display_name(track["artist_name"]),
            ),
        )

        # Enriched genre, when the query joined it in (album_genre). Carried
        # on the card so clients — and the server's suggestion attestation —
        # can see what the track is without another lookup.
        if track.get("album_genre"):
            album.genre = Genre(
                id=genre_id(track["album_genre"].lower()),
                name=track["album_genre"],
            )

        # Add album image if available
        cover_path = self._get_album_image_urls(track["album_id"])
        if cover_path:
            album.image = cover_path

        # Create Track object
        track_obj = Track(
            id=track_id(track["id"]),
            title=track["title"],
            duration=track["duration"],
            album=album,
        )

        # Add performer if available
        track_obj.performer = Artist(
            id=artist_id(track["artist_id"]),
            name=artist_display_name(track["artist_name"]),
        )

        # Add ReplayGain info if available
        if track.get("replaygain_gain") is not None:
            track_obj.replaygain_gain = track["replaygain_gain"]

        if track.get("replaygain_peak") is not None:
            track_obj.replaygain_peak = track["replaygain_peak"]

        # Add playlist track ID if available (for playlist contexts)
        if track.get("playlist_track_id"):
            track_obj.playlist_track_id = track["playlist_track_id"]

        return track_obj

    def _create_track_browse_item(self, track: Dict) -> BrowseItem:
        """Create a BrowseItem for a track"""
        track_metadata = self._create_track_metadata(track)

        return BrowseItem(
            id=track_id(track["id"]),
            name=track["title"],
            can_browse=False,
            can_add=True,
            subname=track["artist_name"],
            track=track_metadata,
        )

    def _create_album_browse_item(self, album: Dict) -> BrowseItem:
        """Create a BrowseItem for an album"""
        # Create album object
        album_obj = Album(
            id=album_id(album["id"]),
            title=album["title"],
            duration=album.get("duration", 0),
            track_count=album.get("track_count", 0),
            artist=Artist(
                id=artist_id(album["artist_id"]),
                name=artist_display_name(album["artist_name"]),
            ),
        )

        # Add image if available
        cover_path = self._get_album_image_urls(album["id"])
        if cover_path:
            album_obj.image = cover_path

        sections_obj = [
            BrowseItem(
                id=album_id(album["id"]),
                name="Tracks",
                can_browse=True,
                can_add=False,
                catalog=Catalog(
                    id=album_id(album["id"]),
                    title=album["title"],
                    can_genre_filter=False,
                    preview_config=Preview(
                        type=PreviewType.TILE_NUMBERED,
                        content_type=PreviewContentType.TRACK,
                        items_count=15,
                        rows_count=1,
                        card_size=CardSize.SMALL,
                    ),
                ),
            )
        ]

        return BrowseItem(
            id=album_id(album["id"]),
            name=album["title"],
            can_browse=True,
            can_add=True,
            subname=album["artist_name"],
            album=album_obj,
            sections=sections_obj,
        )

    def _create_artist_browse_item(self, artist: Dict) -> BrowseItem:
        """Create a BrowseItem for an artist"""
        # Create artist object
        artist_obj = Artist(
            id=artist_id(artist["id"]),
            name=artist_display_name(artist["name"]),
        )

        # Add image if available
        image_path = self._get_artist_image_urls(artist["id"])
        if image_path:
            artist_obj.image = image_path

        sections_obj = sections = [
            BrowseItem(
                id=catalog_id(f"tracks-{artist['id']}"),
                name="Recent Tracks",
                can_browse=True,
                can_add=False,
                catalog=Catalog(
                    id=catalog_id(f"tracks-{artist['id']}"),
                    title=artist["name"],
                    can_genre_filter=False,
                    preview_config=Preview(
                        type=PreviewType.TILE,
                        content_type=PreviewContentType.TRACK,
                        items_count=15,
                        rows_count=1,
                        card_size=CardSize.SMALL,
                    ),
                ),
            ),
            BrowseItem(
                id=artist_id(artist["id"]),
                name="Albums",
                can_browse=True,
                can_add=False,
                catalog=Catalog(
                    id=artist_id(artist["id"]),
                    title=artist["name"],
                    can_genre_filter=False,
                    preview_config=Preview(
                        type=PreviewType.IMAGE_TEXT,
                        content_type=PreviewContentType.ALBUM,
                        items_count=10,
                        rows_count=1,
                        card_size=CardSize.SMALL,
                    ),
                ),
            ),
        ]

        return BrowseItem(
            id=artist_id(artist["id"]),
            name=artist["name"],
            can_browse=True,
            can_add=False,
            artist=artist_obj,
            sections=sections_obj,
        )

    def _create_playlist_browse_item(self, playlist: Dict) -> BrowseItem:
        """Create a BrowseItem for a playlist"""

        owner = Owner(name="Local System", id=user_id("localfiles_system"))

        # Create playlist object
        playlist_obj = Playlist(
            id=playlist_id(playlist["id"]),
            name=playlist["name"],
            description=playlist["description"],
            track_count=playlist.get("track_count", 0),
            owner=owner,
        )

        # Add image if available
        image_path = self._get_playlist_image_urls(playlist["id"])
        if image_path:
            playlist_obj.image = image_path

        sections_obj = [
            BrowseItem(
                id=playlist_id(playlist["id"]),
                name="Tracks",
                can_browse=True,
                can_add=False,
                catalog=Catalog(
                    id=playlist_id(playlist["id"]),
                    title=playlist["name"],
                    can_genre_filter=False,
                    preview_config=Preview(
                        type=PreviewType.TILE,
                        content_type=PreviewContentType.TRACK,
                        items_count=15,
                        rows_count=1,
                        card_size=CardSize.SMALL,
                    ),
                ),
            )
        ]

        return BrowseItem(
            id=playlist_id(playlist["id"]),
            name=playlist["name"],
            can_browse=True,
            can_add=True,
            subname=f"{playlist.get('track_count', 0)} tracks",
            timestamp=playlist["last_updated"],
            playlist=playlist_obj,
            sections=sections_obj,
        )

    def _get_album_image_urls(self, album_id: str) -> Optional[CoverImage]:
        """Get image URLs for an album"""
        # Get album data from database to check if image_url exists
        album = self.db_manager.get_album_by_id(album_id)
        if not album or not album.get("image_url"):
            return None

        # Use the image_url from database (without .jpg extension)
        image_base = album["image_url"].replace(".jpg", "")

        # Define relative paths for album images including the /resource/ prefix
        thumbnail = f"/resource/album/{image_base}_thumbnail.jpg"
        small = f"/resource/album/{image_base}_small.jpg"
        large = f"/resource/album/{image_base}_large.jpg"

        # Check if the image files exist using absolute path for the check
        thumbnail_path = self.artwork_path / f"album/{image_base}_thumbnail.jpg"

        # Only return image URLs if the thumbnail file exists
        if thumbnail_path.exists():
            return CoverImage(thumbnail=thumbnail, small=small, large=large)

        return None

    def _get_artist_image_urls(self, artist_id: str) -> Optional[CoverImage]:
        """Get image URLs for an artist"""
        # Get artist data from database to check if image_url exists
        artist = self.db_manager.get_artist_by_id(artist_id)
        if not artist or not artist.get("image_url"):
            return None

        # Use the image_url from database (without .jpg extension)
        image_base = artist["image_url"].replace(".jpg", "")

        # Define relative paths for artist images including the /resource/ prefix
        thumbnail = f"/resource/artist/{image_base}_thumbnail.jpg"
        small = f"/resource/artist/{image_base}_small.jpg"
        large = f"/resource/artist/{image_base}_large.jpg"

        # Check if the image files exist using absolute path for the check
        thumbnail_path = self.artwork_path / f"artist/{image_base}_thumbnail.jpg"

        # Only return image URLs if the thumbnail file exists
        if thumbnail_path.exists():
            return CoverImage(thumbnail=thumbnail, small=small, large=large)

        return None

    def _get_playlist_image_urls(self, playlist_id: str) -> Optional[CoverImage]:
        """Get image URLs for a playlist"""
        # Get playlist data from database to check if image_url exists
        playlist = self.db_manager.get_playlist_by_id(playlist_id)
        if not playlist or not playlist.get("image_url"):
            return None

        # Use the image_url from database (without .jpg extension)
        image_base = playlist["image_url"].replace(".jpg", "")

        # Define relative paths for playlist images including the /resource/ prefix
        thumbnail = f"/resource/playlist/{image_base}_thumbnail.jpg"
        small = f"/resource/playlist/{image_base}_small.jpg"
        large = f"/resource/playlist/{image_base}_large.jpg"

        # Check if the image files exist using absolute path for the check
        thumbnail_path = self.artwork_path / f"playlist/{image_base}_thumbnail.jpg"

        # Only return image URLs if the thumbnail file exists
        if thumbnail_path.exists():
            return CoverImage(thumbnail=thumbnail, small=small, large=large)

        return None

    async def get_resource_path(self, id: str) -> str | None:
        """Get full path to a resource"""
        # Assuming the ID is the file path
        resource_path = (Path(self.artwork_path) / id).resolve()
        return resource_path.as_posix() if resource_path.exists() else None

    async def get_indexer_status(self) -> dict:
        """Return pipeline progress, stage by stage: ``indexing`` (only while
        a scan is running), ``enrichment`` (only when the enricher is
        enabled), and the embedder's ``clap_audio`` / ``clap_text`` job
        coverage. Every stage carries the same
        total/done/pending/in_progress/failed/coverage_pct shape."""
        from .embedder.embedder_db import AsyncEmbedderDb
        from .enricher.enricher_db import AsyncEnricherDb
        from .indexer.indexer_db import AsyncIndexerDb
        from .worker_utils import stage_status

        result: dict = {}

        try:
            scan = await AsyncIndexerDb(self.config).get_scan_progress()
        except Exception as e:
            logger.warning("get_indexer_status: scan progress failed: %s", e)
            scan = None
        # Progress writes land every ~2s while files are being processed,
        # but a single file can stall a write for much longer (quiescence
        # wait, slow network mount) — the window is a crash guard, not a
        # liveness bound, hence the generous 10 minutes. A scan killed
        # harder than `finally` (OOM, power loss) stops reading as active
        # once the window lapses, or as soon as the next scan starts.
        if (
            scan
            and scan.get("active")
            and time.time() - scan.get("updated_at", 0) < 600
        ):
            total = scan.get("total", 0)
            result["indexing"] = stage_status(
                total, done=min(scan.get("processed", 0), total)
            )

        if self.config.enricher.enabled:
            try:
                result["enrichment"] = await AsyncEnricherDb(
                    self.config
                ).get_enrichment_coverage()
            except Exception as e:
                logger.warning("get_indexer_status: enrichment failed: %s", e)

        # Only when embedding jobs actually drain: with AI search off there
        # is no embedder at all, so leftover jobs would report as pending
        # forever — a never-finishing "Preparing AI search" stage that also
        # blocks suggestion attestation.
        if self.config.ai_search.enabled:
            try:
                result.update(
                    await AsyncEmbedderDb(self.config).get_embedding_coverage()
                )
            except Exception as e:
                logger.warning(
                    "get_indexer_status: embedding coverage failed: %s", e
                )

        return result
