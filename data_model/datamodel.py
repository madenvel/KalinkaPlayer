from typing import Optional, List
from pydantic import (
    BaseModel,
    NonNegativeInt,
    model_serializer,
    field_validator,
    model_validator,
)
from enum import Enum


class EntityType(str, Enum):
    CATALOG = "catalog"
    ALBUM = "album"
    ARTIST = "artist"
    PLAYLIST = "playlist"
    TRACK = "track"
    LABEL = "label"
    GENRE = "genre"
    USER = "user"


class EntityId(BaseModel):
    id: str
    type: EntityType
    source: str

    @model_validator(mode="before")
    @classmethod
    def validate_entity_id_model(cls, values):
        # Handle the case where we receive a string instead of a dict
        if isinstance(values, str):
            return cls.from_string(values).__dict__
        return values

    @field_validator("id", "type", "source", mode="before")
    @classmethod
    def validate_from_string(cls, v, info):
        # If we receive a string for any field and it looks like a full entity ID,
        # parse the entire string and return the appropriate field value
        if isinstance(v, str) and v.startswith("kalinka:") and ":" in v:
            # This is a full entity ID string, parse it
            parts = v.split(":")
            if len(parts) == 4 and parts[0] == "kalinka":
                _, source, type_str, id_ = parts
                # Return the value for the specific field being validated
                if info.field_name == "id":
                    return id_
                elif info.field_name == "type":
                    return EntityType(type_str)
                elif info.field_name == "source":
                    return source
        return v

    @property
    def to_string(self) -> str:
        return f"kalinka:{self.source}:{self.type.value}:{self.id}"

    @classmethod
    def from_string(cls, full_id: str) -> "EntityId":
        # Expected format: kalinka:{source}:{type}:{id}
        parts = full_id.split(":")
        if len(parts) != 4 or parts[0] != "kalinka":
            raise ValueError(f"Invalid full_id format: {full_id}")
        _, source, type_str, id_ = parts
        return cls(id=id_, type=EntityType(type_str), source=source)

    def __hash__(self):
        return hash(self.to_string)

    def __eq__(self, other):
        if isinstance(other, EntityId):
            return self.to_string == other.to_string
        elif isinstance(other, str):
            return self.to_string == other
        return False

    @model_serializer
    def ser_model(self) -> str:
        return self.to_string


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
    id: EntityId
    name: str
    image: Optional[ArtistImage] = None
    album_count: Optional[int] = None


class Label(BaseModel):
    id: EntityId
    name: str


class Genre(BaseModel):
    id: EntityId
    name: str


class Album(BaseModel):
    id: EntityId
    title: str
    duration: Optional[int] = None
    track_count: Optional[int] = None
    image: Optional[AlbumImage] = None
    label: Optional[Label] = None
    genre: Optional[Genre] = None
    artist: Optional[Artist] = None


class Track(BaseModel):
    id: EntityId
    title: str
    # Duration in seconds
    duration: int
    performer: Optional[Artist] = None
    album: Album
    replaygain_peak: Optional[float] = None
    replaygain_gain: Optional[float] = None
    playlist_track_id: Optional[str] = None


class Owner(BaseModel):
    name: str
    id: EntityId


class Playlist(BaseModel):
    id: EntityId
    name: str
    owner: Owner
    image: Optional[PlaylistImage] = None
    description: Optional[str]
    track_count: int


class Preview(BaseModel):
    # Maximum number of items to be shown in the preview section.
    # This is a UI configuration value, not the actual count of items available.
    items_count: Optional[int] = None
    type: PreviewType
    rows_count: Optional[int] = None
    aspect_ratio: Optional[float] = None
    card_size: Optional[CardSize] = None


class Catalog(BaseModel):
    id: EntityId
    title: str
    image: Optional[CatalogImage] = None
    can_genre_filter: bool = False
    description: Optional[str] = ""
    preview_config: Optional[Preview] = None


class BrowseItem(BaseModel):
    id: EntityId
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

    # Used for merging multiple items into a single view
    timestamp: NonNegativeInt = 0

    # Used for additional catalog sections to display alongside the current item
    # Examples: "Similar albums", "From the same artist", "Recommended" etc.
    # Not to be used for preview content in the root catalog
    extra_sections: Optional[List["BrowseItem"]] = None


class BrowseItemList(BaseModel):
    offset: int
    limit: int
    total: int
    items: List[BrowseItem]


def EmptyList(offset, limit) -> BrowseItemList:
    return BrowseItemList(offset=offset, limit=limit, total=0, items=[])
