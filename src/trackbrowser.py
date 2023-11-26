from abc import ABC, abstractmethod
from pydantic import BaseModel, PositiveInt
from enum import Enum
from typing import Callable


class SourceType(Enum):
    QOBUZ = "qobuz"


class AlbumImage(BaseModel):
    small: str = ""
    large: str = ""


class Album(BaseModel):
    title: str
    duration: int = 0
    image: AlbumImage


class Artist(BaseModel):
    name: str


class TrackMetadata(BaseModel):
    id: str
    title: str
    duration: int
    performer: Artist
    album: Album


class TrackInfo(BaseModel):
    track_type: SourceType = SourceType.QOBUZ
    id: str = ""
    link_retriever: Callable[[], str]
    metadata: TrackMetadata = None


class TrackUrl(BaseModel):
    url: str
    format: str
    sample_rate: PositiveInt
    bit_depth: PositiveInt


class CategoryImage(BaseModel):
    small: str = ""
    large: str = ""


class BrowseCategory(BaseModel):
    id: str
    name: str
    can_browse: bool
    needs_input: bool = False
    info: dict = {}
    path: list[str] = []
    image: CategoryImage = CategoryImage()


class TrackBrowser(ABC):
    @abstractmethod
    def list_categories(
        self,
        path: list[str],
        offset=0,
        limit=50,
    ) -> list[BrowseCategory]:
        pass
