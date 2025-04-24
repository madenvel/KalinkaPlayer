from typing import Optional, List
from pydantic import BaseModel
from enum import Enum


class PreviewType(str, Enum):
    IMAGE_TEXT = "image"
    TEXT_ONLY = "text"
    CAROUSEL = "carousel"
    NONE = "none"


class CardSize(str, Enum):
    SMALL = "small"
    LARGE = "large"


class AlbumImage(BaseModel):
    small: Optional[str] = ""
    thumbnail: Optional[str] = ""
    large: Optional[str] = ""


class ArtistImage(BaseModel):
    small: Optional[str] = ""
    thumbnail: Optional[str] = ""
    large: Optional[str] = ""


class CatalogImage(BaseModel):
    small: Optional[str] = ""
    large: Optional[str] = ""
    thumbnail: Optional[str] = ""


class PlaylistImage(BaseModel):
    small: Optional[str] = ""
    large: Optional[str] = ""
    thumbnail: Optional[str] = ""


class Artist(BaseModel):
    id: str
    name: str
    image: Optional[ArtistImage] = None
    album_count: Optional[int] = None


class Label(BaseModel):
    id: str
    name: str


class Genre(BaseModel):
    id: str
    name: str


class Album(BaseModel):
    id: str
    title: str
    duration: Optional[int] = None
    track_count: Optional[int] = None
    image: Optional[AlbumImage] = None
    label: Optional[Label] = None
    genre: Optional[Genre] = None
    artist: Optional[Artist] = None


class Track(BaseModel):
    id: str
    title: str
    duration: int
    performer: Optional[Artist] = None
    album: Album
    replaygain_peak: Optional[float] = None
    replaygain_gain: Optional[float] = None
    playlist_track_id: Optional[str] = None


class Owner(BaseModel):
    name: str
    id: str


class Playlist(BaseModel):
    id: str
    name: str
    owner: Owner
    image: Optional[PlaylistImage] = None
    description: Optional[str]
    track_count: int


class Preview(BaseModel):
    items_count: Optional[int] = None
    type: PreviewType
    rows_count: Optional[int] = None
    aspect_ratio: Optional[float] = None
    card_size: Optional[CardSize] = None


class Catalog(BaseModel):
    id: str
    title: str
    image: Optional[CatalogImage] = None
    can_genre_filter: bool = False
    description: Optional[str] = ""
    preview_config: Optional[Preview] = None


class BrowseItem(BaseModel):
    id: str
    name: str
    url: str
    can_browse: bool = False
    can_add: bool = False
    subname: Optional[str] = None
    album: Optional[Album] = None
    artist: Optional[Artist] = None
    playlist: Optional[Playlist] = None
    catalog: Optional[Catalog] = None
    track: Optional[Track] = None
    extra_sections: Optional[List["BrowseItem"]] = None


class BrowseItemList(BaseModel):
    offset: int
    limit: int
    total: int
    items: List[BrowseItem]


def EmptyList(offset, limit) -> BrowseItemList:
    return BrowseItemList(offset=offset, limit=limit, total=0, items=[])
