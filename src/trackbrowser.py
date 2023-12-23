from abc import ABC, abstractmethod
from pydantic import BaseModel, PositiveInt
from enum import Enum
from typing import Callable, List, Optional

from data_model.datamodel import Track, BrowseItemList


class SourceType(Enum):
    QOBUZ = "qobuz"


class TrackInfo(BaseModel):
    id: str
    link_retriever: Callable[[], str]
    metadata: Optional[Track]


class TrackUrl(BaseModel):
    url: str
    format: str
    sample_rate: PositiveInt
    bit_depth: PositiveInt


class SearchType(str, Enum):
    album = "album"
    track = "track"
    playlist = "playlist"
    artist = "artist"


class TrackBrowser(ABC):
    @abstractmethod
    def search(
        self, type: SearchType, query: str, offset=0, limit=50
    ) -> BrowseItemList:
        pass

    @abstractmethod
    def browse_catalog(
        self, endpoint: str, offset: int = 0, limit: int = 50
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
    def browse_favorite(
        self, type: SearchType, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        pass

    # @abstractmethod
    # def get_favorite_ids(self):
    #     pass
