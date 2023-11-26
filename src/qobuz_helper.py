import time
from qobuz_dl.qopy import Client
from qobuz_dl.bundle import Bundle

from functools import partial

from src.trackbrowser import (
    Album,
    Artist,
    TrackBrowser,
    TrackInfo,
    BrowseCategory,
    TrackUrl,
)

import json


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
        "performer": Artist(name=track["performer"]["name"]),
        "duration": track["duration"],
        "album": Album(title=album_info["title"], image=album_info["image"]),
    }


class QobuzTrackBrowser(TrackBrowser):
    def __init__(self, qobuz_client: Client):
        self.qobuz_client = qobuz_client

    def list_categories(
        self,
        path: list[str],
        offset=0,
        limit=50,
    ) -> list[BrowseCategory]:
        if len(path) == 0:
            return [
                BrowseCategory(
                    id="myweeklyq",
                    name="My Weekly Q",
                    can_browse=True,
                    path=["myweeklyq"],
                )
            ]

        if path[0] == "myweeklyq":
            if len(path) > 1:
                return []

            return self._tracks_to_browse_categories(
                self._get_curated_tracks()["tracks"]["items"]
            )
        elif path[0] in ["album", "playlist", "artist"]:
            if len(path) != 2:
                return []

            id = path[1]

            req = self.qobuz_client.api_call(
                path[0] + "/get", id=id, offset=offset, limit=limit
            )

            if path[0] == "album":
                album_meta = req.copy()
                del album_meta["tracks"]
                return self._tracks_to_browse_categories(
                    req["tracks"]["items"],
                    album_meta=album_meta,
                )

            return []

        elif path[0] == "search":
            if len(path) == 1:
                return [
                    BrowseCategory(
                        "album",
                        "Album",
                        can_browse=True,
                        needs_input=True,
                        path=["search", "album"],
                    ),
                    BrowseCategory(
                        "track",
                        "Track",
                        can_browse=True,
                        needs_input=True,
                        path=["search", "track"],
                    ),
                    BrowseCategory(
                        "artist",
                        "Artist",
                        can_browse=True,
                        needs_input=True,
                        path=["search", "artist"],
                    ),
                    BrowseCategory(
                        "playlist",
                        "Playlist",
                        can_browse=True,
                        needs_input=True,
                        path=["search", "playlist"],
                    ),
                ]
            if path[1] not in ["album", "track", "artist", "playlist"]:
                raise Exception("Path does not exist")
            if len(path) < 3:
                raise Exception("Must provide search query")
            if len(path) == 3:
                return self._search_items(path[1], path[2], offset, limit)
            else:
                return []

    def _tracks_to_browse_categories(self, tracks, album_meta={}):
        return [
            BrowseCategory(
                id=str(track["id"]),
                name=track["title"],
                can_browse=False,
                needs_input=False,
                info={
                    "track": TrackInfo(
                        metadata=metadata_from_track(track, album_meta),
                        link_retriever=partial(
                            get_track_url, self.qobuz_client, track["id"]
                        ),
                    )
                },
            )
            for track in tracks
        ]

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
                can_browse=True,
                info={
                    "album": {
                        "artist": {
                            "name": album["artist"]["name"],
                            "id": album["artist"]["id"],
                        }
                    }
                },
                path=["album", album["id"]],
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

            **Default**: :code:`50`.

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
