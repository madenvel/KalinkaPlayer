import logging
import os
from pathlib import Path
from typing import List, Dict, Optional
import mimetypes

from fastapi import HTTPException
from .config_model import LocalFilesConfig
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
    PlaylistImage,
    Catalog,
)
from data_model.response_model import FavoriteIds, GenreList
from .utils.id_generator import generate_playlist_id
from .utils.image_utils import create_playlist_cover_collage
from .input_module_db import LocalFilesInputModuleDb

logger = logging.getLogger(__name__.split(".")[-1])


class LocalFilesInputModule(InputModule):
    """Local music files input module implementation"""

    def __init__(
        self,
        config: LocalFilesConfig,
        db_manager: LocalFilesInputModuleDb,
        event_emitter: EventEmitter,
    ):
        # Use the specialized LocalFilesInputModuleDb passed from module_setup.py
        self.db_manager = db_manager
        self.event_emitter = event_emitter
        self.artwork_path = config.artwork_path

        # Ensure artwork directories exist
        os.makedirs(os.path.join(self.artwork_path, "album"), exist_ok=True)
        os.makedirs(os.path.join(self.artwork_path, "artist"), exist_ok=True)
        os.makedirs(os.path.join(self.artwork_path, "playlist"), exist_ok=True)

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
            return self._search_playlists(query, offset, limit)
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

    def _search_playlists(self, query: str, offset: int, limit: int) -> BrowseItemList:
        """Search for playlists"""
        playlists, total = self.db_manager.search_playlists(query, offset, limit)

        items = []
        for playlist in playlists:
            items.append(self._create_playlist_browse_item(playlist))

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
        elif endpoint == "playlists":
            return self._browse_playlists(offset, limit)
        else:
            logger.warning(f"Unknown catalog endpoint: {endpoint}")
            return EmptyList(offset, limit)

    def _browse_root(self) -> BrowseItemList:
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
                type=PreviewType.IMAGE_TEXT,
                items_count=10,  # Fixed value: maximum number of items to display in preview
                rows_count=1,
                card_size=CardSize.SMALL,
            )

            catalog = Catalog(
                id="recent",
                title="Recently Added",
                can_genre_filter=False,
                description="Recently added tracks",
                preview_config=preview,
            )

            recent_section = BrowseItem(
                id="recent",
                name="Recently Added",
                url="/catalog/recent",
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
                items_count=10,  # Fixed value: maximum number of items to display in preview
                rows_count=1,
                card_size=CardSize.SMALL,
            )

            catalog = Catalog(
                id="albums",
                title="My Albums",
                can_genre_filter=False,
                description="Browse your album collection",
                preview_config=preview,
            )

            album_section = BrowseItem(
                id="albums",
                name="My Albums",
                url="/catalog/albums",
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
                items_count=10,  # Fixed value: maximum number of items to display in preview
                rows_count=1,
                card_size=CardSize.SMALL,
            )

            catalog = Catalog(
                id="artists",
                title="My Artists",
                can_genre_filter=False,
                description="Browse your artist collection",
                preview_config=preview,
            )

            artist_section = BrowseItem(
                id="artists",
                name="My Artists",
                url="/catalog/artists",
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
                items_count=10,
                rows_count=1,
                card_size=CardSize.SMALL,
            )

            catalog = Catalog(
                id="playlists",
                title="My Playlists",
                can_genre_filter=False,
                description="Browse your playlists",
                preview_config=preview,
            )

            playlist_section = BrowseItem(
                id="playlists",
                name="My Playlists",
                url="/catalog/playlists",
                can_browse=True,
                can_add=False,
                catalog=catalog,
                subname=f"{playlists_total} playlists",
            )

            items.append(playlist_section)

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

    def _browse_playlists(self, offset: int, limit: int) -> BrowseItemList:
        """Browse all playlists"""
        playlists, total = self.db_manager.get_all_playlists(offset, limit)

        items = []
        for playlist in playlists:
            items.append(self._create_playlist_browse_item(playlist))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def browse_album(self, id: str, offset: int = 0, limit: int = 50) -> BrowseItemList:
        """Browse tracks in an album"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return EmptyList(0, 0)

        tracks, total = self.db_manager.get_album_tracks(id, offset, limit)

        items = []
        for track in tracks:
            items.append(self._create_track_browse_item(track))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def browse_artist(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        """Browse albums by an artist"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return EmptyList(0, 0)

        albums, total = self.db_manager.get_artist_albums(id, offset, limit)

        items = []
        for album in albums:
            items.append(self._create_album_browse_item(album))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def browse_playlist(
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

    def get_track_info(self, track_ids: List[str]) -> List[TrackInfo]:
        """Get track info for a list of track IDs"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return []

        tracks = self.db_manager.get_tracks_by_ids(track_ids)

        # Create a dictionary of tracks indexed by ID for quick lookup
        track_dict = {track["id"]: track for track in tracks}

        # Maintain the same order as track_ids
        result = []
        for track_id in track_ids:
            if track_id in track_dict:
                track = track_dict[track_id]
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
        """List favorites - for playlists, returns all user playlists"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return EmptyList(0, 0)

        if type == SearchType.playlist:
            # Return all user playlists as favorites
            return self.playlist_user_list(offset, limit)
        else:
            # For other types, return an empty list as before
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
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        album = self.db_manager.get_album_by_id(id)
        if not album:
            logger.warning(f"Album not found: {id}")
            raise HTTPException(status_code=404, detail=f"Album not found: {id}")

        return self._create_album_browse_item(album)

    def artist_get(self, id: str) -> BrowseItem:
        """Get artist details"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        artist = self.db_manager.get_artist_by_id(id)
        if not artist:
            logger.warning(f"Artist not found: {id}")
            raise HTTPException(status_code=404, detail=f"Artist not found: {id}")

        return self._create_artist_browse_item(artist)

    def track_get(self, id: str) -> BrowseItem:
        """Get track details"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        track = self.db_manager.get_track_by_id(id)
        if not track:
            logger.warning(f"Track not found: {id}")
            raise HTTPException(status_code=404, detail=f"Track not found: {id}")

        return self._create_track_browse_item(track)

    def playlist_get(self, id: str) -> BrowseItem:
        """Get playlist details"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        playlist = self.db_manager.get_playlist_by_id(id)
        if not playlist:
            logger.warning(f"Playlist not found: {id}")
            raise HTTPException(status_code=404, detail=f"Playlist not found: {id}")

        return self._create_playlist_browse_item(playlist)

    def playlist_user_list(self, offset: int = 0, limit: int = 25) -> BrowseItemList:
        """List user playlists"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return EmptyList(0, 0)

        playlists, total = self.db_manager.get_all_playlists(offset, limit)

        items = []
        for playlist in playlists:
            items.append(self._create_playlist_browse_item(playlist))

        return BrowseItemList(offset=offset, limit=limit, total=total, items=items)

    def playlist_create(self, name: str, description: str) -> Playlist:
        """Create playlist"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")
        # Generate playlist ID using the name and system as creator
        playlist_id = generate_playlist_id(name, "localfiles_system")
        # Create the playlist record
        self.db_manager.create_playlist(
            playlist_id, name, description, "localfiles_system"
        )

        # Get the created playlist
        playlist = self.db_manager.get_playlist_by_id(playlist_id)
        if not playlist:
            logger.error(f"Failed to retrieve created playlist: {playlist_id}")
            raise HTTPException(status_code=500, detail="Failed to create playlist")

        # Create the Owner object required by the Playlist model
        from data_model.datamodel import Owner

        owner = Owner(name="Local System", id="localfiles_system")

        return Playlist(
            id=playlist_id,
            name=name,
            description=description,
            track_count=playlist.get("track_count", 0),
            # last_updated=playlist["last_updated"],
            owner=owner,
        )

    def playlist_update(
        self, id: str, name: Optional[str], description: Optional[str]
    ) -> Playlist:
        """Update playlist"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        self.db_manager.update_playlist(id, name, description)
        playlist = self.db_manager.get_playlist_by_id(id)

        if not playlist:
            logger.warning(f"Playlist not found: {id}")
            raise HTTPException(status_code=404, detail=f"Playlist not found: {id}")

        # Create the Owner object required by the Playlist model
        from data_model.datamodel import Owner

        owner = Owner(name="Local System", id="localfiles_system")

        playlist_obj = Playlist(
            id=playlist["id"],
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

    def playlist_delete(self, id: str):
        """Delete playlist"""
        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            return

        self.db_manager.delete_playlist(id)

    def playlist_add_tracks(
        self, id: str, track_ids: List[str], allow_duplicates: bool = False
    ) -> Playlist:
        """Add tracks to playlist"""

        if not self.db_manager.is_good():
            logger.warning("Database is not initialized or corrupted")
            raise HTTPException(status_code=503, detail="Database service unavailable")

        # Add tracks to the playlist
        tracks_added = self.db_manager.add_tracks_to_playlist(
            id, track_ids, allow_duplicates
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

        # Create the Owner object required by the Playlist model
        from data_model.datamodel import Owner

        owner = Owner(name="Local System", id="localfiles_system")

        # Create Playlist object
        playlist_obj = Playlist(
            id=playlist["id"],
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

    def playlist_remove_tracks(
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

        # Create the Owner object required by the Playlist model
        from data_model.datamodel import Owner

        owner = Owner(name="Local System", id="localfiles_system")

        playlist_obj = Playlist(
            id=playlist["id"],
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

        # Add playlist track ID if available (for playlist contexts)
        if track.get("playlist_track_id"):
            track_obj.playlist_track_id = track["playlist_track_id"]

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

    def _create_playlist_browse_item(self, playlist: Dict) -> BrowseItem:
        """Create a BrowseItem for a playlist"""
        # Create the Owner object required by the Playlist model
        from data_model.datamodel import Owner

        owner = Owner(name="Local System", id="localfiles_system")

        # Create playlist object
        playlist_obj = Playlist(
            id=playlist["id"],
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

        return BrowseItem(
            id=playlist["id"],
            name=playlist["name"],
            url=f"/playlist/{playlist['id']}",
            can_browse=True,
            can_add=True,
            subname=f"{playlist.get('track_count', 0)} tracks",
            playlist=playlist_obj,
        )

    def _get_album_image_urls(self, album_id: str) -> Optional[AlbumImage]:
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
        thumbnail_path = os.path.join(
            self.artwork_path, f"album/{image_base}_thumbnail.jpg"
        )

        # Only return image URLs if the thumbnail file exists
        if os.path.exists(thumbnail_path):
            return AlbumImage(thumbnail=thumbnail, small=small, large=large)

        return None

    def _get_artist_image_urls(self, artist_id: str) -> Optional[ArtistImage]:
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
        thumbnail_path = os.path.join(
            self.artwork_path, f"artist/{image_base}_thumbnail.jpg"
        )

        # Only return image URLs if the thumbnail file exists
        if os.path.exists(thumbnail_path):
            return ArtistImage(thumbnail=thumbnail, small=small, large=large)

        return None

    def _get_playlist_image_urls(self, playlist_id: str) -> Optional[PlaylistImage]:
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
        thumbnail_path = os.path.join(
            self.artwork_path, f"playlist/{image_base}_thumbnail.jpg"
        )

        # Only return image URLs if the thumbnail file exists
        if os.path.exists(thumbnail_path):
            return PlaylistImage(thumbnail=thumbnail, small=small, large=large)

        return None

    def get_resource_path(self, id: str) -> str | None:
        """Get full path to a resource"""
        # Assuming the ID is the file path
        resource_path = (Path(self.artwork_path) / id).resolve().as_posix()
        return resource_path if os.path.exists(resource_path) else None
