from typing import List, Optional
from pydantic import BaseModel

from data_model.datamodel import Genre, Track


class FavoriteIds(BaseModel):
    albums: List[str] = []
    artists: List[str] = []
    tracks: List[str] = []
    playlists: List[str] = []


class PlayerState(BaseModel):
    state: Optional[str] = None
    current_track: Optional[Track] = None
    index: Optional[int] = None
    progress: Optional[float] = None


class FavoriteAddedEvent(BaseModel):
    id: str
    type: str


class FavoriteRemovedEvent(BaseModel):
    id: str
    type: str


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
