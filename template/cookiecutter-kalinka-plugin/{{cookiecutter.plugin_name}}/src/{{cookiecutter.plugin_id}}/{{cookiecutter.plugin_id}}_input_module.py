from typing import List
from kalinka_plugin_sdk.datamodel import (
    BrowseItem,
    BrowseItemList,
    EntityId,
    FavoriteIds,
    GenreList,
    Playlist,
)
from kalinka_plugin_sdk.inputmodule import InputModule, SearchType, TrackInfo

from .config_model import {{ cookiecutter.plugin_class_prefix }}Config


class {{ cookiecutter.plugin_class_prefix }}InputModule(InputModule):
    def __init__(self, config: {{ cookiecutter.plugin_class_prefix }}Config):
        self.config = config

    def module_name(self) -> str:
        return "{{ cookiecutter.plugin_display_name }}"

    def search(
        self, type: SearchType, query: str, offset=0, limit=50
    ) -> BrowseItemList:
        raise NotImplementedError

    def browse(
        self,
        entity_id: EntityId,
        offset: int = 0,
        limit: int = 50,
        genre_ids: List[EntityId] = [],
    ) -> BrowseItemList:
        raise NotImplementedError

    def get_track_info(self, track_ids: List[str]) -> List[TrackInfo]:
        raise NotImplementedError

    def list_favorite(
        self, type: SearchType, filter: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        raise NotImplementedError

    def get_favorite_ids(self) -> FavoriteIds:
        raise NotImplementedError

    def add_to_favorite(self, id: str):
        raise NotImplementedError

    def remove_from_favorite(self, id: str):
        raise NotImplementedError

    def list_genre(self, offset: int, limit: int) -> GenreList:
        raise NotImplementedError

    def get(self, entity_id: EntityId) -> BrowseItem:
        raise NotImplementedError

    def playlist_user_list(self, offset: int = 0, limit: int = 25) -> BrowseItemList:
        raise NotImplementedError

    def playlist_create(self, name: str, description: str) -> Playlist:
        raise NotImplementedError

    def playlist_update(
        self, id: str, name: str | None, description: str | None
    ) -> Playlist:
        raise NotImplementedError

    def playlist_delete(self, id: str):
        raise NotImplementedError

    def playlist_add_tracks(
        self, id: str, track_ids: List[str], allow_duplicates: bool
    ) -> Playlist:
        raise NotImplementedError

    def playlist_remove_tracks(
        self, id: str, playlist_track_ids: List[str]
    ) -> Playlist:
        raise NotImplementedError

    def get_resource_path(self, id: str) -> str | None:
        raise NotImplementedError
