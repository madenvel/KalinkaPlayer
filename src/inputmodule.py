from abc import ABC, abstractmethod
from pydantic import BaseModel, PositiveInt
from enum import Enum
from typing import Callable, List, Optional

from data_model.datamodel import BrowseItem, Track, BrowseItemList
from data_model.response_model import FavoriteIds, GenreList


class TrackInfo(BaseModel):
    id: str
    link_retriever: Callable[[], str]
    metadata: Optional[Track]


class TrackUrl(BaseModel):
    url: str
    format: str


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
    def browse_catalog(
        self,
        endpoint: str,
        offset: int = 0,
        limit: int = 50,
        genre_ids: List[int] = [],
    ) -> BrowseItemList:
        pass

    @abstractmethod
    def browse_album(self, id: str, offset: int = 0, limit: int = 50) -> BrowseItemList:
        pass

    @abstractmethod
    def browse_playlist(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        pass

    @abstractmethod
    def browse_artist(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        pass

    @abstractmethod
    def get_track_info(self, track_ids: List[str]) -> List[TrackInfo]:
        pass

    @abstractmethod
    def list_favorite(
        self, type: SearchType, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        pass

    @abstractmethod
    def get_favorite_ids(self) -> FavoriteIds:
        pass

    @abstractmethod
    def add_to_favorite(self, type: SearchType, id: str):
        pass

    @abstractmethod
    def remove_from_favorite(self, type: SearchType, id: str):
        pass

    @abstractmethod
    def list_genre(self, offset: int = 0, limit: int = 25) -> GenreList:
        pass

    @abstractmethod
    def album_get(self, id: str) -> BrowseItem:
        pass

    @abstractmethod
    def playlist_get(self, id: str) -> BrowseItem:
        pass

    @abstractmethod
    def artist_get(self, id: str) -> BrowseItem:
        pass

    @abstractmethod
    def track_get(self, id: str) -> BrowseItem:
        pass
