from typing import List, Optional
from pydantic import BaseModel, PositiveInt

from data_model.datamodel import EntityId, Genre, Track


class FavoriteIds(BaseModel):
    albums: List[EntityId] = []
    artists: List[EntityId] = []
    tracks: List[EntityId] = []
    playlists: List[EntityId] = []


class AudioInfo(BaseModel):
    sample_rate: int
    bits_per_sample: int
    channels: int
    duration_ms: int


class PlaybackMode(BaseModel):
    shuffle: bool
    repeat_single: bool
    repeat_all: bool


class PlayerState(BaseModel):
    state: Optional[str] = None
    current_track: Optional[Track] = None
    index: Optional[int] = None
    position: Optional[int] = None
    message: Optional[str] = None
    audio_info: Optional[AudioInfo] = None
    mime_type: Optional[str] = None
    timestamp: PositiveInt = 0


class FavoriteAddedEvent(BaseModel):
    id: EntityId


class FavoriteRemovedEvent(BaseModel):
    id: EntityId


class ErrorResponse(BaseModel):
    error: str
    message: str
    status_code: int


class SuccessResponse(BaseModel):
    message: str
    status_code: int


class GenreList(BaseModel):
    offset: int
    limit: int
    total: int
    items: List[Genre]


class TrackList(BaseModel):
    offset: int
    limit: int
    total: int
    items: List[Track]


class LastUpdate(BaseModel):
    favorite_tracks_ts: int = 0
    favorite_albums_ts: int = 0
    favorite_artists_ts: int = 0
    favorite_playlists_ts: int = 0
