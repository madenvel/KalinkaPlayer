import logging
import os
from pathlib import Path
from typing import List, Dict, Optional, Callable
import time
import mimetypes

from src.inputmodule import InputModule, SearchType, TrackInfo, TrackUrl
from src.async_common import EventEmitter
from data_model.datamodel import (
    BrowseItem,
    BrowseItemList,
    Track,
    Album,
    AlbumImage,
    Artist,
    ArtistImage,
    Preview,
    PreviewType,
    CardSize,
    EmptyList,
    Playlist,
)
from data_model.response_model import FavoriteIds, GenreList, LastUpdate

logger = logging.getLogger(__name__.split(".")[-1])


class LocalFilesInputModule(InputModule):
    """Local music files input module implementation"""

    def __init__(self, config, db_manager, event_emitter: EventEmitter):
        self.config = config
        self.db_manager = db_manager
        self.event_emitter = event_emitter
        self.artwork_path = config["artwork_path"]

        # Initialize mime types for serving files
        mimetypes.init()
        mimetypes.add_type("audio/flac", ".flac")

    def module_name(self) -> str:
        """Return the name of the module"""
        return "localfiles"

    def search(
        self, type: SearchType, query: str, offset=0, limit=50
    ) -> BrowseItemList:
        """Search for items in the local database"""
        if type == SearchType.track:
            return self._search_tracks(query, offset, limit)
        elif type == SearchType.album:
            return self._search_albums(query, offset, limit)
        elif type == SearchType.artist:
            return self._search_artists(query, offset, limit)
        elif type == SearchType.playlist:
            # Playlists not supported yet
            return EmptyList(offset, limit)
        else:
            logger.warning(f"Unsupported search type: {type}")
            return EmptyList(offset, limit)

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

    def browse_catalog(
        self, endpoint: str, offset: int = 0, limit: int = 50, genre_ids: List[int] = []
    ) -> BrowseItemList:
        """Browse the catalog endpoints"""
        if endpoint == "":
            return self._browse_root()
        elif endpoint == "recent":
            return self._browse_recently_added(offset, limit)
        elif endpoint == "albums":
            return self._browse_albums(offset, limit)
        elif endpoint == "artists":
            return self._browse_artists(offset, limit)
        else:
            logger.warning(f"Unknown catalog endpoint: {endpoint}")
            return EmptyList(offset, limit)

    def _browse_root(self) -> BrowseItemList:
        """Return the root catalog with main sections"""
        recent_tracks, recent_total = self.db_manager.get_recently_added_tracks(0, 10)
        albums, albums_total = self.db_manager.get_all_albums(0, 10)
        artists, artists_total = self.db_manager.get_all_artists(0, 10)

        # Create the main sections
        items = []

        # Recently Added section
        if recent_total > 0:
            recent_section = BrowseItem(
                id="recent",
                name="Recently Added",
                url="/catalog/recent",
                can_browse=True,
                can_add=False,
                catalog=None,
                album=None,
                artist=None,
                track=None,
                subname=f"{recent_total} tracks",
            )

            preview = Preview(
                type=PreviewType.IMAGE_TEXT,
                items_count=10,  # Fixed value: maximum number of items to display in preview
                rows_count=1,
                card_size=CardSize.SMALL,
            )

            recent_section.catalog = {
                "id": "recent",
                "title": "Recently Added",
                "can_genre_filter": False,
                "description": "Recently added tracks",
                "preview_config": preview,
            }

            items.append(recent_section)

        # Albums section
        if albums_total > 0:
            album_section = BrowseItem(
                id="albums",
                name="My Albums",
                url="/catalog/albums",
                can_browse=True,
                can_add=False,
                catalog=None,
                album=None,
                artist=None,
                track=None,
                subname=f"{albums_total} albums",
            )

            preview = Preview(
                type=PreviewType.IMAGE_TEXT,
                items_count=10,  # Fixed value: maximum number of items to display in preview
                rows_count=1,
                card_size=CardSize.SMALL,
            )

            album_section.catalog = {
                "id": "albums",
                "title": "My Albums",
                "can_genre_filter": False,
                "description": "Browse your album collection",
                "preview_config": preview,
            }

            items.append(album_section)

        # Artists section
        if artists_total > 0:
            artist_section = BrowseItem(
                id="artists",
                name="My Artists",
                url="/catalog/artists",
                can_browse=True,
                can_add=False,
                catalog=None,
                album=None,
                artist=None,
                track=None,
                subname=f"{artists_total} artists",
            )

            preview = Preview(
                type=PreviewType.IMAGE_TEXT,
                items_count=10,  # Fixed value: maximum number of items to display in preview
                rows_count=1,
                card_size=CardSize.SMALL,
            )

            artist_section.catalog = {
                "id": "artists",
                "title": "My Artists",
                "can_genre_filter": False,
                "description": "Browse your artist collection",
                "preview_config": preview,
            }

            items.append(artist_section)

        return BrowseItemList(offset=0, limit=10, total=len(items), items=items)

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

    def browse_album(self, id: str, offset: int = 0, limit: int = 50) -> BrowseItemList:
        """Browse tracks in an album"""
        tracks, total = self.db_manager.get_album_tracks(id, offset, limit)

        items = []
        for track in tracks:
            items.append(self._create_track_browse_item(track))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def browse_artist(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        """Browse albums by an artist"""
        albums, total = self.db_manager.get_artist_albums(id, offset, limit)

        items = []
        for album in albums:
            items.append(self._create_album_browse_item(album))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def browse_playlist(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        """Browse tracks in a playlist (not supported)"""
        return EmptyList(offset, limit)

    def get_track_info(self, track_ids: List[str]) -> List[TrackInfo]:
        """Get track info for a list of track IDs"""
        tracks = self.db_manager.get_tracks_by_ids(track_ids)

        result = []
        for track in tracks:
            track_metadata = self._create_track_metadata(track)

            # Create a link retriever function for this track
            def create_link_retriever(track_path, track_format):
                def link_retriever():
                    return TrackUrl(url=f"file://{track_path}", format=track_format)

                return link_retriever

            result.append(
                TrackInfo(
                    id=track["id"],
                    link_retriever=create_link_retriever(
                        track["file_path"], track["format"]
                    ),
                    metadata=track_metadata,
                )
            )

        return result

    def list_favorite(
        self, type: SearchType, filter: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        """List favorites (not supported)"""
        return EmptyList(offset, limit)

    def get_favorite_ids(self) -> FavoriteIds:
        """Get favorite IDs (not supported)"""
        return FavoriteIds(tracks=[], albums=[], artists=[], playlists=[])

    def add_to_favorite(self, type: SearchType, id: str):
        """Add to favorites (not supported)"""
        logger.warning("Favorites are not supported in local files input module")

    def remove_from_favorite(self, type: SearchType, id: str):
        """Remove from favorites (not supported)"""
        logger.warning("Favorites are not supported in local files input module")

    def list_genre(self, offset: int = 0, limit: int = 25) -> GenreList:
        """List genres (not implemented yet)"""
        return GenreList(total=0, offset=offset, limit=limit, items=[])

    def album_get(self, id: str) -> BrowseItem:
        """Get album details"""
        album = self.db_manager.get_album_by_id(id)
        if not album:
            logger.warning(f"Album not found: {id}")
            return None

        return self._create_album_browse_item(album)

    def artist_get(self, id: str) -> BrowseItem:
        """Get artist details"""
        artist = self.db_manager.get_artist_by_id(id)
        if not artist:
            logger.warning(f"Artist not found: {id}")
            return None

        return self._create_artist_browse_item(artist)

    def track_get(self, id: str) -> BrowseItem:
        """Get track details"""
        track = self.db_manager.get_track_by_id(id)
        if not track:
            logger.warning(f"Track not found: {id}")
            return None

        return self._create_track_browse_item(track)

    def playlist_get(self, id: str) -> BrowseItem:
        """Get playlist details (not supported)"""
        logger.warning("Playlists are not supported in local files input module")
        return None

    def playlist_user_list(self, offset: int = 0, limit: int = 25) -> BrowseItemList:
        """List user playlists (not supported)"""
        return EmptyList(offset, limit)

    def playlist_create(self, name: str, description: str) -> Playlist:
        """Create playlist (not supported)"""
        logger.warning("Playlist creation not supported in local files input module")
        return None

    def playlist_update(
        self, id: str, name: Optional[str], description: Optional[str]
    ) -> Playlist:
        """Update playlist (not supported)"""
        logger.warning("Playlist updates not supported in local files input module")
        return None

    def playlist_delete(self, id: str):
        """Delete playlist (not supported)"""
        logger.warning("Playlist deletion not supported in local files input module")

    def playlist_add_tracks(
        self, id: str, track_ids: List[str], allow_duplicates: bool = False
    ) -> Playlist:
        """Add tracks to playlist (not supported)"""
        logger.warning(
            "Playlist modification not supported in local files input module"
        )
        return None

    def playlist_remove_tracks(
        self, id: str, playlist_track_ids: List[str]
    ) -> Playlist:
        """Remove tracks from playlist (not supported)"""
        logger.warning(
            "Playlist modification not supported in local files input module"
        )
        return None

    def _create_track_metadata(self, track: Dict) -> Track:
        """Create a Track object from database data"""
        # Create album object
        album = Album(
            id=track["album_id"],
            title=track["album_title"],
            artist=Artist(id=track["artist_id"], name=track["artist_name"]),
        )

        # Add album image if available
        cover_path = self._get_album_image_urls(track["album_id"])
        if cover_path:
            album.image = cover_path

        # Create Track object
        track_obj = Track(
            id=track["id"],
            title=track["title"],
            duration=track["duration"],
            album=album,
        )

        # Add performer if available
        track_obj.performer = Artist(id=track["artist_id"], name=track["artist_name"])

        # Add ReplayGain info if available
        if track.get("replaygain_gain") is not None:
            track_obj.replaygain_gain = track["replaygain_gain"]

        if track.get("replaygain_peak") is not None:
            track_obj.replaygain_peak = track["replaygain_peak"]

        return track_obj

    def _create_track_browse_item(self, track: Dict) -> BrowseItem:
        """Create a BrowseItem for a track"""
        track_metadata = self._create_track_metadata(track)

        return BrowseItem(
            id=track["id"],
            name=track["title"],
            url=f"/track/{track['id']}",
            can_browse=False,
            can_add=True,
            subname=track["artist_name"],
            track=track_metadata,
        )

    def _create_album_browse_item(self, album: Dict) -> BrowseItem:
        """Create a BrowseItem for an album"""
        # Create album object
        album_obj = Album(
            id=album["id"],
            title=album["title"],
            duration=album.get("duration", 0),
            track_count=album.get("track_count", 0),
            artist=Artist(id=album["artist_id"], name=album["artist_name"]),
        )

        # Add image if available
        cover_path = self._get_album_image_urls(album["id"])
        logger.info(f"Album ID: {album['id']}, Cover path: {cover_path}")
        if cover_path:
            album_obj.image = cover_path

        return BrowseItem(
            id=album["id"],
            name=album["title"],
            url=f"/album/{album['id']}",
            can_browse=True,
            can_add=True,
            subname=album["artist_name"],
            album=album_obj,
        )

    def _create_artist_browse_item(self, artist: Dict) -> BrowseItem:
        """Create a BrowseItem for an artist"""
        # Create artist object
        artist_obj = Artist(id=artist["id"], name=artist["name"])

        # Add image if available
        image_path = self._get_artist_image_urls(artist["id"])
        if image_path:
            artist_obj.image = image_path

        return BrowseItem(
            id=artist["id"],
            name=artist["name"],
            url=f"/artist/{artist['id']}",
            can_browse=True,
            can_add=False,
            artist=artist_obj,
        )

    def _get_album_image_urls(self, album_id: str) -> Optional[AlbumImage]:
        """Get image URLs for an album"""
        # Define relative paths for album images including the /resource/ prefix
        thumbnail = f"/resource/album/{album_id}_thumbnail.jpg"
        small = f"/resource/album/{album_id}_small.jpg"
        large = f"/resource/album/{album_id}_large.jpg"

        # Check if the image files exist using absolute path for the check
        # but without the /resource/ prefix
        thumbnail_path = os.path.join(
            self.artwork_path, f"album/{album_id}_thumbnail.jpg"
        )

        # Only return image URLs if the thumbnail file exists
        if os.path.exists(thumbnail_path):
            return AlbumImage(thumbnail=thumbnail, small=small, large=large)

        return None

    def _get_artist_image_urls(self, artist_id: str) -> Optional[ArtistImage]:
        """Get image URLs for an artist"""
        # Define relative paths for artist images including the /resource/ prefix
        thumbnail = f"/resource/artist/{artist_id}_thumbnail.jpg"
        small = f"/resource/artist/{artist_id}_small.jpg"
        large = f"/resource/artist/{artist_id}_large.jpg"

        # Check if the image files exist using absolute path for the check
        # but without the /resource/ prefix
        thumbnail_path = os.path.join(
            self.artwork_path, f"artist/{artist_id}_thumbnail.jpg"
        )

        # Only return image URLs if the thumbnail file exists
        if os.path.exists(thumbnail_path):
            return ArtistImage(thumbnail=thumbnail, small=small, large=large)

        return None

    def get_resource_path(self, id: str) -> str:
        """Get full path to a resource"""
        # Assuming the ID is the file path
        return (Path(self.artwork_path) / id).resolve()
