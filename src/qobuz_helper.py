import time
from qobuz_dl.qopy import Client
from qobuz_dl.bundle import Bundle

from functools import partial

from src.trackbrowser import (
    Album,
    AlbumImage,
    Artist,
    Genre,
    Label,
    SourceType,
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

    def search(self, type: str, query: str, offset=0, limit=50) -> list[BrowseCategory]:
        return self._search_items(type, query, offset, limit)

    def browse(
        self, endpoint: str, offset: int = 0, limit: int = 50
    ) -> list[BrowseCategory]:
        if endpoint == "":
            return [
                BrowseCategory(
                    id="myweeklyq",
                    name="My Weekly Q",
                    url="/myweeklyq",
                    can_browse=True,
                    can_add=True,
                ),
                BrowseCategory(
                    id="favorite",
                    name="My Favorite",
                    url="/favorite",
                    can_browse=True,
                    can_add=False,
                ),
            ]
        points = endpoint.split("/")
        if points[0] == "myweeklyq":
            return self._tracks_to_browse_categories(
                self._get_curated_tracks()["tracks"]["items"]
            )
        if points[0] in ["album", "playlist"]:
            req = self.qobuz_client.api_call(
                points[0] + "/get", id=points[1], offset=offset, limit=limit
            )

            if points[0] == "album":
                album_meta = req.copy()
                del album_meta["tracks"]
                return self._tracks_to_browse_categories(
                    req["tracks"]["items"],
                    album_meta=album_meta,
                )

            return self._albums_to_browse_category(
                self.qobuz_client.get_album_tracks(points[1])
            )

        if points[0] == "favorite":
            if len(points) > 1 and points[1] in ["albums", "tracks", "artists"]:
                return self.get_user_favorites(points[1], offset, limit)
            else:
                return [
                    BrowseCategory(
                        id="favorite",
                        name="My Favorite Albums",
                        url="/favorite/albums",
                        can_browse=True,
                        can_add=False,
                    ),
                    BrowseCategory(
                        id="favorite",
                        name="My Favorite Tracks",
                        url="/favorite/tracks",
                        can_browse=True,
                        can_add=True,
                    ),
                ]

        return []

    def get_user_favorites(self, item_type, offset, limit):
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "favorite/getUserFavorites",
            params={"type": item_type, "offset": offset, "limit": limit},
        )

        if response.ok == True:
            if item_type == "tracks":
                return self._tracks_to_browse_categories(
                    response.json()["tracks"]["items"]
                )

            if item_type == "albums":
                return self._albums_to_browse_category(
                    response.json()["albums"]["items"]
                )
        return []

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
                    url="/track/" + str(track["id"]),
                    image=album["image"],
                )
            )
        return result

    def _search_items(self, item_type, query, offset, limit):
        req = self.qobuz_client.api_call(
            item_type + "/search", query=query, limit=limit, offset=offset
        )

        if item_type == "track":
            return self._tracks_to_browse_categories(req["tracks"]["items"])

        if item_type == "album":
            return self._albums_to_browse_category(req["albums"]["items"])

    def _albums_to_browse_category(self, albums):
        return [
            BrowseCategory(
                id=album["id"],
                name=album["title"],
                subname=album["artist"]["name"],
                url="/album/" + album["id"],
                can_browse=True,
                can_add=True,
                image=album["image"],
            )
            for album in albums
        ]

    def _get_curated_tracks(
        self, limit: int = None, offset: int = None
    ) -> list[dict[str, any]]:
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

        r = self.qobuz_client.session.get(
            self.qobuz_client.base + epoint, params=params
        )
        return r.json()
