from pydantic import BaseModel, PositiveInt, ConfigDict
from enum import Enum
from typing import Awaitable, Callable, List, Optional, Protocol, runtime_checkable

from .datamodel import (
    BrowseItem,
    EntityId,
    Playlist,
    Track,
    BrowseItemList,
    FavoriteIds,
    GenreList,
)


class TrackUrl(BaseModel):
    """
    Represents a track's streaming URL and format information.

    Attributes:
        url (str): The streaming URL for the track
        format (str): The audio format/codec (e.g., "mp3", "flac", "aac")
    """

    url: str
    format: str


class TrackInfo(BaseModel):
    """
    Complete track information including metadata and URL retrieval.

    This class provides all the information needed to play a track, including
    a callable that can retrieve the actual streaming URL when needed.

    Attributes:
        id (EntityId): Unique identifier for the track
        link_retriever: Callable[[], TrackUrl] | Callable[[], Awaitable[TrackUrl]]: Function that returns the track's streaming URL
        metadata (Optional[Track]): Track metadata (title, artist, album, etc.)
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: EntityId
    link_retriever: Callable[[], TrackUrl] | Callable[[], Awaitable[TrackUrl]]
    metadata: Optional[Track]


class SearchType(str, Enum):
    """
    Enumeration of searchable content types within input modules.

    Values:
        album: Search for albums
        track: Search for individual tracks
        playlist: Search for playlists
        artist: Search for artists
    """

    album = "album"
    track = "track"
    playlist = "playlist"
    artist = "artist"


@runtime_checkable
class InputModule(Protocol):
    """
    Protocol for Kalinka audio input modules.

    This protocol defines the interface that all input modules must implement to provide
    audio content to the Kalinka player system. Input modules can represent various
    audio sources such as streaming services, local files, radio stations, etc.

    Each input module is responsible for:
    - Browsing and searching audio content
    - Managing user favorites
    - Handling playlists
    - Providing track metadata and playback URLs
    - Managing genres and categorization
    """

    def module_name(self) -> str:
        """
        Return the unique name identifier for this input module.

        Returns:
            str: A unique string identifier for this module (e.g., "spotify", "localfiles")
        """
        ...

    async def search(
        self, type: SearchType, query: str, offset=0, limit=50
    ) -> BrowseItemList:
        """
        Search for content within this input module.

        Args:
            type (SearchType): The type of content to search for (album, track, playlist, artist)
            query (str): The search query string
            offset (int, optional): Number of results to skip for pagination. Defaults to 0.
            limit (int, optional): Maximum number of results to return. Defaults to 50.

        Returns:
            BrowseItemList: A list of matching items found by the search
        """
        ...

    async def browse(
        self,
        entity_id: EntityId,
        offset: PositiveInt = 0,
        limit: PositiveInt = 50,
        genre_ids: List[EntityId] = [],
    ) -> BrowseItemList:
        """
        Browse content within a specific entity (e.g., album contents, artist's albums).

        Args:
            entity_id (EntityId): The ID of the entity to browse into. Use "kalinka:<device_name>:catalog:root"
                                 to access the root level of the module.
            offset (PositiveInt, optional): Number of results to skip for pagination. Defaults to 0.
            limit (PositiveInt, optional): Maximum number of results to return. Defaults to 50.
            genre_ids (List[EntityId], optional): List of genre IDs to filter by. Defaults to [].

        Returns:
            BrowseItemList: A list of items contained within the specified entity
        """
        ...

    async def get_track_info(self, track_ids: List[str]) -> List[TrackInfo]:
        """
        Retrieve detailed track information including playback URLs.

        This method is called when the player needs to actually play tracks,
        providing both metadata and a callable to get the streaming URL.

        Args:
            track_ids (List[str]): List of track IDs to get information for

        Returns:
            List[TrackInfo]: List of track information objects containing
                     metadata and URL retrievers for each requested track, suitable to insert into the play queue.
        """
        ...

    async def list_favorite(
        self, type: SearchType, filter: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        """
        List user's favorite items of a specific type.

        Args:
            type (SearchType): The type of favorites to list (album, track, playlist, artist)
            filter (str): Additional filter criteria for the favorites
            offset (int, optional): Number of results to skip for pagination. Defaults to 0.
            limit (int, optional): Maximum number of results to return. Defaults to 50.

        Returns:
            BrowseItemList: A list of the user's favorite items of the specified type
        """
        ...

    async def get_favorite_ids(self) -> FavoriteIds:
        """
        Retrieve all favorite item IDs for the current user.

        Returns:
            FavoriteIds: Object containing collections of favorite IDs grouped by type
        """
        ...

    async def add_to_favorite(self, id: str):
        """
        Add an item to the user's favorites.

        Args:
            id (str): The ID of the item to add to favorites
        """
        ...

    async def remove_from_favorite(self, id: str):
        """
        Remove an item from the user's favorites.

        Args:
            id (str): The ID of the item to remove from favorites
        """
        ...

    async def list_genre(self, offset: int, limit: int) -> GenreList:
        """
        List available genres in this input module.

        Args:
            offset (int): Number of results to skip for pagination
            limit (int): Maximum number of results to return

        Returns:
            GenreList: A list of available genres
        """
        ...

    async def get(self, entity_id: EntityId) -> BrowseItem:
        """
        Get detailed information about a specific entity.

        Args:
            entity_id (EntityId): The ID of the entity to retrieve

        Returns:
            BrowseItem: Detailed information about the requested entity
        """
        ...

    async def playlist_user_list(
        self, offset: int = 0, limit: int = 25
    ) -> BrowseItemList:
        """
        List user-created playlists.

        Args:
            offset (int, optional): Number of results to skip for pagination. Defaults to 0.
            limit (int, optional): Maximum number of results to return. Defaults to 25.

        Returns:
            BrowseItemList: A list of user-created playlists
        """
        ...

    async def playlist_create(self, name: str, description: str) -> Playlist:
        """
        Create a new playlist.

        Args:
            name (str): The name for the new playlist
            description (str): A description for the new playlist

        Returns:
            Playlist: The newly created playlist object
        """
        ...

    async def playlist_update(
        self, id: str, name: Optional[str], description: Optional[str]
    ) -> Playlist:
        """
        Update an existing playlist's metadata.

        Args:
            id (str): The ID of the playlist to update
            name (Optional[str]): New name for the playlist (None to keep unchanged)
            description (Optional[str]): New description for the playlist (None to keep unchanged)

        Returns:
            Playlist: The updated playlist object
        """
        ...

    async def playlist_delete(self, id: str):
        """
        Delete a playlist.

        Args:
            id (str): The ID of the playlist to delete
        """
        ...

    async def playlist_add_tracks(
        self, id: str, track_ids: List[str], allow_duplicates: bool
    ) -> Playlist:
        """
        Add tracks to an existing playlist.

        Args:
            id (str): The ID of the playlist to add tracks to
            track_ids (List[str]): List of track IDs to add to the playlist
            allow_duplicates (bool): Whether to allow duplicate tracks in the playlist

        Returns:
            Playlist: The updated playlist object with new tracks added
        """
        ...

    async def playlist_remove_tracks(
        self, id: str, playlist_track_ids: List[str]
    ) -> Playlist:
        """
        Remove tracks from an existing playlist.

        Args:
            id (str): The ID of the playlist to remove tracks from
            playlist_track_ids (List[str]): List of playlist track IDs to remove
                                          (these are playlist-specific IDs, not track IDs)

        Returns:
            Playlist: The updated playlist object with tracks removed
        """
        ...

    async def get_resource_path(self, id: str) -> str | None:
        """
        Get the file system path for a local resource.

        This method is primarily used by local file input modules to provide
        direct file system access to audio files.

        Args:
            id (str): The ID of the resource to get the path for

        Returns:
            str | None: The file system path to the resource, or None if not available
        """
        ...
