from typing import List
from qobuz_dl.qopy import Client
from qobuz_dl.bundle import Bundle

from functools import partial

from src.trackbrowser import (
    Album,
    AlbumImage,
    Artist,
    BrowseCategoryList,
    EmptyList,
    Genre,
    Label,
    SearchType,
    TrackBrowser,
    TrackInfo,
    BrowseCategory,
    TrackUrl,
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


class QobuzTrackBrowser(TrackBrowser):
    def __init__(self, qobuz_client: Client):
        self.qobuz_client = qobuz_client

    def search(
        self, type: SearchType, query: str, offset=0, limit=50
    ) -> list[BrowseCategory]:
        return self._search_items(type, query, offset, limit)

    def browse_album(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseCategoryList:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "album/get",
            params={"album_id": id, "offset": offset, "limit": limit},
        )

        if response.ok != True:
            return EmptyList(offset, limit)

        rjson = response.json()

        album_meta = rjson.copy()
        del album_meta["tracks"]

        return {
            "offset": offset,
            "limit": limit,
            "total": rjson["tracks"]["total"],
            "items": self._tracks_to_browse_categories(
                rjson["tracks"]["items"],
                album_meta=album_meta,
            ),
        }

    def browse_playlist(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseCategoryList:
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

        return BrowseCategoryList(
            offset=offset,
            limit=limit,
            total=rjson["tracks"]["total"],
            items=self._tracks_to_browse_categories(
                rjson["tracks"]["items"],
            ),
        )

    def browse_favorite(
        self, type: SearchType, offset: int = 0, limit: int = 50
    ) -> BrowseCategoryList:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "favorite/getUserFavorites",
            params={"type": type.value + "s", "offset": offset, "limit": limit},
        )

        if response.ok != True:
            return EmptyList(offset, limit)

        rjson = response.json()

        if type == SearchType.track:
            return BrowseCategoryList(
                offset=offset,
                limit=limit,
                total=rjson["tracks"]["total"],
                items=self._tracks_to_browse_categories(
                    response.json()["tracks"]["items"]
                ),
            )

        if type == SearchType.album:
            return BrowseCategoryList(
                offset=offset,
                limit=limit,
                total=rjson["albums"]["total"],
                items=self._albums_to_browse_category(
                    response.json()["albums"]["items"]
                ),
            )

    def browse_catalog(
        self, endpoint: str, offset: int = 0, limit: int = 50
    ) -> BrowseCategoryList:
        if endpoint == "":
            return BrowseCategoryList(
                offset=offset,
                limit=limit,
                total=4,
                items=[
                    BrowseCategory(
                        id="new-releases",
                        name="New Releases",
                        url="/catalog/new-releases",
                        can_browse=True,
                        can_add=False,
                    ),
                    BrowseCategory(
                        id="qobuz-playlists",
                        name="Qobuz Playlists",
                        url="/catalog/qobuz-playlists",
                        can_browse=True,
                        can_add=False,
                    ),
                    # BrowseCategory(
                    #     id="playlist-by-category",
                    #     name="Playlist By Category",
                    #     url="/catalog/playlist-by-category",
                    #     can_browse=True,
                    #     can_add=False,
                    # ),
                    BrowseCategory(
                        id="myweeklyq",
                        name="My Weekly Q",
                        description="Every Friday, a selection of discoveries curated especially for you.",
                        url="/catalog/myweeklyq",
                        can_browse=True,
                        can_add=True,
                        sub_categories_count=30,
                        image=AlbumImage(
                            small="https://static.qobuz.com/images/dynamic/weekly_small_en.png",
                            large="https://static.qobuz.com/images/dynamic/weekly_large_en.png",
                        ),
                    ),
                    BrowseCategory(
                        id="recent-releases",
                        name="Still Trending",
                        url="/catalog/recent-releases",
                        can_browse=True,
                        can_add=False,
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
    ) -> BrowseCategoryList:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "/album/getFeatured",
            params={"type": type, "offset": offset, "limit": limit},
        )

        if response.ok != True:
            return EmptyList(offset, limit)

        rjson = response.json()

        return BrowseCategoryList(
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

        return BrowseCategoryList(
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
                BrowseCategory(
                    id=str(track["id"]),
                    name=track["title"],
                    subname=track["performer"]["name"],
                    can_browse=False,
                    can_add=True,
                    sub_categories_count=0,
                    url="/track/" + str(track["id"]),
                    image=album["image"],
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

        rjson = response.json()

        if item_type == SearchType.track:
            return BrowseCategoryList(
                offset=offset,
                limit=limit,
                total=rjson["tracks"]["total"],
                items=self._tracks_to_browse_categories(rjson["tracks"]["items"]),
            )

        if item_type == SearchType.album:
            return BrowseCategoryList(
                offset=offset,
                limit=limit,
                total=rjson["albums"]["total"],
                items=self._albums_to_browse_category(rjson["albums"]["items"]),
            )

        if item_type == SearchType.playlist:
            return BrowseCategoryList(
                offset=offset,
                limit=limit,
                total=rjson["playlists"]["total"],
                items=self._playlists_to_browse_category(rjson["playlists"]["items"]),
            )

    def _albums_to_browse_category(self, albums):
        return [
            BrowseCategory(
                id=album["id"],
                name=album["title"],
                subname=album["artist"]["name"],
                url="/album/" + album["id"],
                can_browse=True,
                can_add=True,
                sub_categories_count=album["tracks_count"],
                image=album["image"],
            )
            for album in albums
        ]

    def _playlists_to_browse_category(self, playlists):
        return [
            BrowseCategory(
                id=str(playlist["id"]),
                name=playlist["name"],
                subname=playlist["owner"]["name"],
                description=playlist["description"],
                url="/playlist/" + str(playlist["id"]),
                can_browse=True,
                can_add=True,
                sub_categories_count=playlist["tracks_count"],
                image={
                    "small": playlist["images150"][0],
                    "large": playlist["image_rectangle"][0],
                    "thumbnail": playlist["image_rectangle_mini"][0],
                },
            )
            for playlist in playlists
        ]

    def _get_curated_tracks(
        self, offset: int = 0, limit: int = 30
    ) -> BrowseCategoryList:
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

        return BrowseCategoryList(
            offset=offset,
            limit=limit,
            total=len(rjson["tracks"]["items"]),
            items=self._tracks_to_browse_categories(rjson["tracks"]["items"]),
        )
