from abc import ABC, abstractmethod
from pydantic import BaseModel, PositiveInt
from enum import Enum
from typing import Callable, List, Optional

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
    url: str
    format: str


class TrackInfo(BaseModel):
    id: EntityId
    link_retriever: Callable[[], TrackUrl]
    metadata: Optional[Track]


class SearchType(str, Enum):
    album = "album"
    track = "track"
    playlist = "playlist"
    artist = "artist"


class InputModule(ABC):
    @abstractmethod
    def module_name(self) -> str:
        pass

    @abstractmethod
    def search(
        self, type: SearchType, query: str, offset=0, limit=50
    ) -> BrowseItemList:
        pass

    @abstractmethod
    def browse(
        self,
        entity_id: EntityId,
        offset: PositiveInt = 0,
        limit: PositiveInt = 50,
        genre_ids: List[EntityId] = [],
    ) -> BrowseItemList:
        pass

    @abstractmethod
    def get_track_info(self, track_ids: List[str]) -> List[TrackInfo]:
        pass

    @abstractmethod
    def list_favorite(
        self, type: SearchType, filter: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        pass

    @abstractmethod
    def get_favorite_ids(self) -> FavoriteIds:
        pass

    @abstractmethod
    def add_to_favorite(self, id: str):
        pass

    @abstractmethod
    def remove_from_favorite(self, id: str):
        pass

    @abstractmethod
    def list_genre(self, offset: int, limit: int) -> GenreList:
        pass

    @abstractmethod
    def get(self, entity_id: EntityId) -> BrowseItem:
        pass

    @abstractmethod
    def playlist_user_list(self, offset: int = 0, limit: int = 25) -> BrowseItemList:
        pass

    @abstractmethod
    def playlist_create(self, name: str, description: str) -> Playlist:
        pass

    @abstractmethod
    def playlist_update(
        self, id: str, name: Optional[str], description: Optional[str]
    ) -> Playlist:
        pass

    @abstractmethod
    def playlist_delete(self, id: str):
        pass

    @abstractmethod
    def playlist_add_tracks(
        self, id: str, track_ids: List[str], allow_duplicates: bool
    ) -> Playlist:
        pass

    @abstractmethod
    def playlist_remove_tracks(
        self, id: str, playlist_track_ids: List[str]
    ) -> Playlist:
        pass

    @abstractmethod
    def get_resource_path(self, id: str) -> str | None:
        pass
