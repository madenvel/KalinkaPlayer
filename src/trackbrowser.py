from abc import ABC, abstractmethod
from pydantic import BaseModel, PositiveInt
from enum import Enum
from typing import Callable, List, Optional, Union


class SourceType(Enum):
    QOBUZ = "qobuz"


class AlbumImage(BaseModel):
    small: Optional[str] = ""
    thumbnail: Optional[str] = ""
    large: Optional[str] = ""


class Label(BaseModel):
    id: str
    name: str


class Genre(BaseModel):
    id: str
    name: str


class Album(BaseModel):
    id: str
    title: str
    duration: int = 0
    image: AlbumImage
    label: Optional[Label] = None
    genre: Optional[Genre] = None


class Artist(BaseModel):
    name: str
    id: str


class TrackMetadata(BaseModel):
    id: str
    title: str
    duration: int
    performer: Artist
    album: Album


class TrackInfo(BaseModel):
    id: str
    link_retriever: Callable[[], str]
    metadata: Optional[TrackMetadata]


class TrackUrl(BaseModel):
    url: str
    format: str
    sample_rate: PositiveInt
    bit_depth: PositiveInt


class BrowseCategory(BaseModel):
    id: str
    name: str
    subname: Optional[str] = None
    description: Optional[str] = None
    url: str = ""
    can_browse: bool = False
    can_add: bool = False
    sub_categories_count: int = 0
    tags: Optional[List[str]] = None
    # TODO: Make a list of images instead of a single image for playlists
    image: Optional[AlbumImage] = AlbumImage()


class BrowseCategoryList(BaseModel):
    offset: int
    limit: int
    total: int
    items: List[BrowseCategory]


def EmptyList(offset, limit):
    return BrowseCategoryList(offset=offset, limit=limit, total=0, items=[])


class SearchType(str, Enum):
    album = "album"
    track = "track"
    playlist = "playlist"
    artist = "artist"


class TrackBrowser(ABC):
    @abstractmethod
    def search(
        self, type: SearchType, query: str, offset=0, limit=50
    ) -> BrowseCategoryList:
        pass

    @abstractmethod
    def browse_catalog(
        self, endpoint: str, offset: int = 0, limit: int = 50
    ) -> BrowseCategoryList:
        pass

    @abstractmethod
    def browse_album(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseCategoryList:
        pass

    @abstractmethod
    def browse_playlist(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseCategoryList:
        pass

    @abstractmethod
    def browse_artist(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseCategoryList:
        pass

    @abstractmethod
    def get_track_info(self, track_ids: List[str]) -> List[TrackInfo]:
        pass

    @abstractmethod
    def browse_favorite(
        self, type: SearchType, offset: int = 0, limit: int = 50
    ) -> BrowseCategoryList:
        pass

    # @abstractmethod
    # def get_favorite_ids(self):
    #     pass
