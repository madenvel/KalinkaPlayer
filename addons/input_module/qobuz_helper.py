from typing import List
from qobuz_dl.qopy import Client
from qobuz_dl.bundle import Bundle

from functools import partial
from data_model.response_model import (
    FavoriteAddedEvent,
    FavoriteRemovedEvent,
    FavoriteIds,
)
from src.events import EventType
from src.rpiasync import EventEmitter

from src.inputmodule import (
    SearchType,
    InputModule,
    TrackInfo,
    TrackUrl,
)

from data_model.datamodel import (
    Album,
    AlbumImage,
    Artist,
    ArtistImage,
    BrowseItem,
    BrowseItemList,
    Catalog,
    CatalogImage,
    EmptyList,
    Genre,
    Label,
    Owner,
    Playlist,
    PlaylistImage,
    Track,
)

import json


TrackInfoCache: dict[str, TrackInfo] = {}
AlbumInfoCache: dict[str, Album] = {}


def get_config():
    with open("rpi.conf", "r") as f:
        return json.load(fp=f)


def get_client():
    config = get_config()
    email = config["qobuz"]["email"]
    password = config["qobuz"]["password_hash"]
    bundle = Bundle()

    app_id = bundle.get_app_id()
    secrets = [secret for secret in bundle.get_secrets().values() if secret]
    client = Client(email, password, app_id, secrets)
    return client


def extract_track_format(track):
    mime_type = track["mime_type"].split("/")[1]
    sampling_rate = int(track["sampling_rate"] * 1000)
    bit_depth = track["bit_depth"]

    return (mime_type, sampling_rate, bit_depth)


def get_track_url(qobuz_client, id) -> str:
    track = qobuz_client.get_track_url(id, fmt_id=27)
    (format, sample_rate, bit_depth) = extract_track_format(track)
    track_url = TrackUrl(
        url=track["url"], format=format, sample_rate=sample_rate, bit_depth=bit_depth
    )
    return track_url


def metadata_from_track(track, album_meta={}):
    album_info = track.get("album", album_meta)
    return {
        "id": str(track["id"]),
        "title": track["title"],
        "performer": Artist(
            name=track["performer"]["name"], id=str(track["performer"]["id"])
        )
        if "performer" in track
        else Artist(
            name=album_info["artist"].get("name", None),
            id=str(album_info["artist"].get("id", None)),
        ),
        "duration": track["duration"],
        "album": Album(
            id=str(album_info["id"]),
            title=album_info["title"],
            image=album_info["image"],
            label=Label(
                id=str(album_info["label"]["id"]), name=album_info["label"]["name"]
            ),
            genre=Genre(
                id=str(album_info["genre"]["id"]), name=album_info["genre"]["name"]
            ),
        ),
    }


class QobuzInputModule(InputModule):
    def __init__(self, qobuz_client: Client, event_emitter: EventEmitter):
        self.qobuz_client = qobuz_client
        self.event_emitter = event_emitter

    def search(
        self, type: SearchType, query: str, offset=0, limit=50
    ) -> list[BrowseItem]:
        return self._search_items(type, query, offset, limit)

    def browse_album(self, id: str, offset: int = 0, limit: int = 50) -> BrowseItemList:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "album/get",
            params={"album_id": id, "offset": offset, "limit": limit},
        )

        if response.ok != True:
            return EmptyList(offset, limit)

        rjson = response.json()

        album_meta = rjson.copy()
        del album_meta["tracks"]

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=rjson["tracks"]["total"],
            items=self._tracks_to_browse_categories(
                rjson["tracks"]["items"],
                album_meta=album_meta,
            ),
        )

    def browse_playlist(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "playlist/get",
            params={
                "playlist_id": id,
                "offset": offset,
                "limit": limit,
                "extra": "tracks",
            },
        )

        if response.ok != True:
            return EmptyList(offset, limit)

        rjson = response.json()

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=rjson["tracks"]["total"],
            items=self._tracks_to_browse_categories(
                rjson["tracks"]["items"],
            ),
        )

    def browse_artist(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "artist/get",
            params={
                "artist_id": id,
                "offset": offset,
                "limit": limit,
                "extra": "albums",
            },
        )

        if response.ok != True:
            return EmptyList(offset, limit)

        rjson = response.json()

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=rjson["albums"]["total"],
            items=self._albums_to_browse_category(response.json()["albums"]["items"]),
        )

    def list_favorite(
        self, type: SearchType, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        if type == SearchType.playlist:
            response = self.qobuz_client.session.get(
                self.qobuz_client.base + "playlist/getUserPlaylists",
                params={"offset": offset, "limit": limit},
            )
        else:
            response = self.qobuz_client.session.get(
                self.qobuz_client.base + "favorite/getUserFavorites",
                params={"type": type.value + "s", "offset": offset, "limit": limit},
            )

        if response.ok != True:
            return EmptyList(offset, limit)

        return self._format_list_response(response.json(), offset, limit)

    def browse_catalog(
        self, endpoint: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        if endpoint == "":
            return BrowseItemList(
                offset=offset,
                limit=limit,
                total=4,
                items=[
                    BrowseItem(
                        id="new-releases",
                        name="New Releases",
                        url="/catalog/new-releases",
                        can_browse=True,
                        can_add=False,
                        catalog=Catalog(id="new-releases", title="New Releases"),
                    ),
                    BrowseItem(
                        id="qobuz-playlists",
                        name="Qobuz Playlists",
                        url="/catalog/qobuz-playlists",
                        can_browse=True,
                        can_add=False,
                        catalog=Catalog(id="qobuz-playlists", title="Qobuz Playlists"),
                    ),
                    # BrowseCategory(
                    #     id="playlist-by-category",
                    #     name="Playlist By Category",
                    #     url="/catalog/playlist-by-category",
                    #     can_browse=True,
                    #     can_add=False,
                    # ),
                    BrowseItem(
                        id="myweeklyq",
                        name="My Weekly Q",
                        url="/catalog/myweeklyq",
                        can_browse=True,
                        can_add=True,
                        catalog=Catalog(
                            id="myweeklyq",
                            title="My Weekly Q",
                            description="Every Friday, a selection of discoveries curated especially for you.",
                            image=CatalogImage(
                                small="https://static.qobuz.com/images/dynamic/weekly_small_en.png",
                                large="https://static.qobuz.com/images/dynamic/weekly_large_en.png",
                            ),
                        ),
                    ),
                    BrowseItem(
                        id="recent-releases",
                        name="Still Trending",
                        url="/catalog/recent-releases",
                        can_browse=True,
                        can_add=False,
                        catalog=Catalog(id="recent-releases", title="Still Trending"),
                    ),
                ],
            )
        elif endpoint == "new-releases":
            return self._get_new_releases("new-releases-full", offset, limit)
        elif endpoint == "qobuz-playlists":
            return self._get_qobuz_playlists(offset, limit)
        elif endpoint == "recent-releases":
            return self._get_new_releases("recent-releases", offset, limit)
        elif endpoint == "myweeklyq":
            return self._get_curated_tracks(offset, limit)

    def _get_new_releases(
        self,
        type: str,
        offset: int = 0,
        limit: int = 10,
    ) -> BrowseItemList:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "/album/getFeatured",
            params={"type": type, "offset": offset, "limit": limit},
        )

        if response.ok != True:
            return EmptyList(offset, limit)

        rjson = response.json()

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=rjson["albums"]["total"],
            items=self._albums_to_browse_category(response.json()["albums"]["items"]),
        )

    def _get_qobuz_playlists(self, offset: int = 0, limit: int = 10):
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "/playlist/getFeatured",
            params={"type": "editor-picks", "offset": offset, "limit": limit},
        )

        if response.ok != True:
            return EmptyList(offset, limit)

        rjson = response.json()

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=rjson["playlists"]["total"],
            items=self._playlists_to_browse_category(
                response.json()["playlists"]["items"]
            ),
        )

    def get_track_info(self, track_ids: list[str]) -> list[TrackInfo]:
        return [self._track_to_track_info(str(track_id)) for track_id in track_ids]

    def _track_to_track_info(self, track_id: str):
        if track_id in TrackInfoCache:
            return TrackInfoCache[track_id]
        track = self.qobuz_client.get_track_meta(track_id)
        track_info = TrackInfo(
            id=track_id,
            link_retriever=partial(get_track_url, self.qobuz_client, track["id"]),
            metadata=metadata_from_track(track),
        )

        TrackInfoCache[track_id] = track_info
        return track_info

    def _tracks_to_browse_categories(self, tracks, album_meta={}):
        result = []
        for track in tracks:
            track_id = str(track["id"])
            TrackInfoCache[track_id] = TrackInfo(
                id=track_id,
                link_retriever=partial(get_track_url, self.qobuz_client, track["id"]),
                metadata=metadata_from_track(track, album_meta),
            )
            album = track.get("album", album_meta)
            result.append(
                BrowseItem(
                    id=str(track["id"]),
                    name=track["title"],
                    subname=track["performer"]["name"]
                    if "performer" in track
                    else album.get("artist", {"name": None})["name"],
                    can_browse=False,
                    can_add=True,
                    url="/track/" + str(track["id"]),
                    track=Track(
                        id=str(track["id"]),
                        title=track["title"],
                        duration=track["duration"],
                        performer=Artist(
                            id=str(track["performer"]["id"]),
                            name=track["performer"]["name"],
                        )
                        if "performer" in track
                        else None,
                        album=Album(
                            id=str(album["id"]),
                            title=album["title"],
                            artist=Artist(
                                name=album["artist"]["name"],
                                id=str(album["artist"]["id"]),
                            ),
                            image=AlbumImage(**album["image"]),
                        ),
                    ),
                )
            )
        return result

    def _search_items(self, item_type, query, offset, limit):
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + item_type.value + "/search",
            params={"query": query, "limit": limit, "offset": offset},
        )

        if response.ok != True:
            return EmptyList(offset, limit)

        return self._format_list_response(response.json(), offset, limit)

    def _format_list_response(self, items, offset, limit):
        if "tracks" in items:
            return BrowseItemList(
                offset=offset,
                limit=limit,
                total=items["tracks"]["total"],
                items=self._tracks_to_browse_categories(items["tracks"]["items"]),
            )

        if "albums" in items:
            return BrowseItemList(
                offset=offset,
                limit=limit,
                total=items["albums"]["total"],
                items=self._albums_to_browse_category(items["albums"]["items"]),
            )

        if "playlists" in items:
            return BrowseItemList(
                offset=offset,
                limit=limit,
                total=items["playlists"]["total"],
                items=self._playlists_to_browse_category(items["playlists"]["items"]),
            )

        if "artists" in items:
            return BrowseItemList(
                offset=offset,
                limit=limit,
                total=items["artists"]["total"],
                items=self._artists_to_browse_category(items["artists"]["items"]),
            )

        return EmptyList(offset, limit)

    def _artists_to_browse_category(self, artists):
        return [
            BrowseItem(
                id=str(artist["id"]),
                name=artist["name"],
                subname=None,
                url="/artist/" + str(artist["id"]),
                can_browse=True,
                can_add=False,
                artist=Artist(
                    id=str(artist["id"]),
                    name=artist["name"],
                    image=ArtistImage(
                        thumbnail=artist["image"].get("small", None),
                        small=artist["image"].get("medium", None),
                        large=artist["image"].get("large", None),
                    )
                    if artist["image"]
                    else None,
                    album_count=artist["albums_count"],
                ),
            )
            for artist in artists
        ]

    def _albums_to_browse_category(self, albums):
        return [
            BrowseItem(
                id=str(album["id"]),
                name=album["title"],
                subname=album["artist"]["name"],
                url="/album/" + album["id"],
                can_browse=True,
                can_add=True,
                album=Album(
                    id=str(album["id"]),
                    title=album["title"],
                    artist=Artist(
                        name=album["artist"]["name"], id=str(album["artist"]["id"])
                    ),
                    image=AlbumImage(
                        thumbnail=album["image"].get("thumbnail", None),
                        small=album["image"].get("small", None),
                        large=album["image"].get("large", None),
                    )
                    if "image" in album and album["image"]
                    else None,
                    duration=album["duration"],
                    track_count=album["tracks_count"],
                    genre=Genre(
                        id=str(album["genre"]["id"]), name=album["genre"]["name"]
                    ),
                ),
            )
            for album in albums
        ]

    def _playlists_to_browse_category(self, playlists):
        return [
            BrowseItem(
                id=str(playlist["id"]),
                name=playlist["name"],
                subname=playlist["owner"]["name"],
                url="/playlist/" + str(playlist["id"]),
                can_browse=True,
                can_add=True,
                playlist=self._qobuz_playlist_to_playlist(playlist),
            )
            for playlist in playlists
        ]

    def _qobuz_playlist_to_playlist(self, playlist):
        return Playlist(
            id=str(playlist["id"]),
            name=playlist["name"],
            owner=Owner(
                name=playlist["owner"]["name"],
                id=str(playlist["owner"]["id"]),
            ),
            image=PlaylistImage(
                small=playlist.get("images150", [None])[0],
                large=playlist.get(
                    "image_rectangle", playlist.get("images300", [None])
                )[0],
                thumbnail=playlist.get(
                    "image_rectangle_mini", playlist.get("images", [None])
                )[0],
            ),
            description=playlist["description"],
            track_count=playlist["tracks_count"],
        )

    def _get_curated_tracks(self, offset: int = 0, limit: int = 30) -> BrowseItemList:
        """
        Get weekly curated tracks for the user.

        Parameters
        ----------
        limit : `int`, keyword-only, optional
            The maximum number of tracks to return.

            **Default**: :code:`30`.

        offset : `int`, keyword-only, optional
            The index of the first track to return. Use with `limit`
            to get the next page of tracks.

            **Default**: :code:`0`.

        Returns
        -------
        tracks : `list`
            Curated tracks.
        """

        epoint = "dynamic-tracks/get"
        params = {"type": "weekly", "limit": limit, "offset": offset}

        response = self.qobuz_client.session.get(
            self.qobuz_client.base + epoint, params=params
        )

        if response.ok != True:
            return EmptyList(offset, limit)

        rjson = response.json()

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=len(rjson["tracks"]["items"]),
            items=self._tracks_to_browse_categories(rjson["tracks"]["items"]),
        )

    def get_favorite_ids(self) -> FavoriteIds:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "favorite/getUserFavoriteIds",
            params={"limit": 5000},
        )

        if response.ok != True:
            return FavoriteIds()

        rjson = response.json()

        return FavoriteIds(
            albums=rjson["albums"],
            artists=[str(id) for id in rjson["artists"]],
            tracks=[str(id) for id in rjson["tracks"]],
            playlists=self._get_favorite_playlist_ids(),
        )

    def _get_favorite_playlist_ids(self) -> list[str]:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "favorite/getUserPlaylists",
            params={"limit": 500},
        )

        if response.ok != True:
            return FavoriteIds()

        rjson = response.json()

        return [str(playlist["id"]) for playlist in rjson["playlists"]["items"]]

    def add_to_favorite(self, type: SearchType, id: str):
        if type == SearchType.playlist:
            endpoint = "playlist/subscribe"
            params = {"playlist_id": id}
        else:
            endpoint = "favorite/create"
            params = {type.value + "_ids": id}

        response = self.qobuz_client.session.post(
            self.qobuz_client.base + endpoint, params=params
        )

        if response.ok != True:
            return {"message": response.text}

        rjson = response.json()

        if "status" not in rjson or rjson["status"] != "success":
            return {"message": response.text}

        self.event_emitter.dispatch(
            EventType.FavoriteAdded,
            FavoriteAddedEvent(id=id, type=type.value).model_dump(),
        )

        return {"message": "Ok"}

    def remove_from_favorite(self, type: SearchType, id: str):
        if type == SearchType.playlist:
            endpoint = "playlist/unsubscribe"
            params = {"playlist_id": id}
        else:
            endpoint = "favorite/delete"
            params = {type.value + "_ids": id}

        response = self.qobuz_client.session.post(
            self.qobuz_client.base + endpoint, params=params
        )

        if response.ok != True:
            return {"message": response.text}

        rjson = response.json()

        if "status" not in rjson or rjson["status"] != "success":
            return {"message": response.text}

        self.event_emitter.dispatch(
            EventType.FavoriteRemoved,
            FavoriteRemovedEvent(id=id, type=type.value).model_dump(),
        )

        return {"message": "Ok"}
