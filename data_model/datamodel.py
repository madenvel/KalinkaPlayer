from typing import Optional, List
from pydantic import BaseModel


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


class Catalog(BaseModel):
    id: str
    title: str
    image: Optional[CatalogImage] = None
    can_genre_filter: bool = False
    description: Optional[str] = ""


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


class BrowseItemList(BaseModel):
    offset: int
    limit: int
    total: int
    items: List[BrowseItem]


def EmptyList(offset, limit) -> BrowseItemList:
    return BrowseItemList(offset=offset, limit=limit, total=0, items=[])
