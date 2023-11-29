from abc import ABC, abstractmethod
from pydantic import BaseModel, PositiveInt
from enum import Enum
from typing import Callable


class SourceType(Enum):
    QOBUZ = "qobuz"


class AlbumImage(BaseModel):
    small: str | None = None
    thumbnail: str | None = None
    large: str | None = None
    back: str | None = None


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
    label: Label | None
    genre: Genre | None


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
    metadata: TrackMetadata = None


class TrackUrl(BaseModel):
    url: str
    format: str
    sample_rate: PositiveInt
    bit_depth: PositiveInt


class BrowseCategory(BaseModel):
    id: str
    name: str
    subname: str = ""
    comment: str = ""
    url: str = ""
    image: AlbumImage = AlbumImage()


class TrackBrowser(ABC):
    @abstractmethod
    def search(self, type: str, query: str, offset=0, limit=50) -> list[BrowseCategory]:
        pass

    @abstractmethod
    def browse(
        self, endpoint: str, offset: int = 0, limit: int = 50
    ) -> list[BrowseCategory]:
        pass

    @abstractmethod
    def get_track_info(self, track_ids: list[str]) -> list[TrackInfo]:
        pass
