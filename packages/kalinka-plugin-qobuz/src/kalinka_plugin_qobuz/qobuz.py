import copy
import hashlib
import json
import logging
import time
from functools import partial
from typing import List, Optional

import httpx
from pydantic import BaseModel, PositiveInt

from .bundle import Bundle
from .config_model import QobuzAudioFormat, QobuzConfig

from kalinka_plugin_sdk.api import EventEmitterAPI
from kalinka_plugin_sdk.datamodel import (
    Album,
    Artist,
    BrowseItem,
    BrowseItemList,
    CardSize,
    Catalog,
    CoverImage,
    EmptyList,
    EntityId,
    EntityType,
    FavoriteIds,
    Genre,
    GenreList,
    Label,
    Owner,
    Playlist,
    Preview,
    PreviewContentType,
    PreviewType,
    Track,
)
from kalinka_plugin_sdk.events import (
    EventType,
    FavoriteAddedEvent,
    FavoriteRemovedEvent,
)
from kalinka_plugin_sdk.inputmodule import (
    InputModule,
    SearchType,
    TrackInfo,
    TrackUrl,
)

logger = logging.getLogger(__name__.split(".")[-1])


class AuthenticationError(Exception):
    pass


class IneligibleError(Exception):
    pass


class InvalidAppIdError(Exception):
    pass


class InvalidAppSecretError(Exception):
    pass


class InvalidQuality(Exception):
    pass


class NonStreamable(Exception):
    pass


class RetryTransport(httpx.HTTPTransport):
    def __init__(self, read_retries=3, **kwargs):
        super().__init__(**kwargs)
        self.read_retries = read_retries
        self.backoff_factor = 0.5

    def handle_request(self, request) -> httpx.Response:
        read_retries = 0
        last_exception = None
        while read_retries < self.read_retries:
            try:
                response = super().handle_request(request)
                if response.status_code >= 500 and response.status_code < 600:
                    read_retries += 1
                    time.sleep(self.backoff_factor * read_retries)
                    logger.warning(
                        f"Retry {read_retries} due to status code={response.status_code}"
                    )
                    continue
                return response
            except (
                httpx.ProtocolError,
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.ConnectTimeout,
                httpx.ProxyError,
                httpx.ConnectError,
                httpx.ReadError,
            ) as exc:
                last_exception = exc
                read_retries += 1
                time.sleep(self.backoff_factor * read_retries)
                logger.warning(f"Retry {read_retries} due to {exc}")
        # If all retries failed, raise the last exception
        if last_exception is not None:
            raise last_exception
        raise RuntimeError("handle_request failed without exception")


# The code below is partially based on the code from
# qobuz-dl by vitiko98, fc7


def artist_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.ARTIST, source="qobuz")


def album_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.ALBUM, source="qobuz")


def track_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.TRACK, source="qobuz")


def playlist_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.PLAYLIST, source="qobuz")


def label_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.LABEL, source="qobuz")


def genre_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.GENRE, source="qobuz")


def user_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.USER, source="qobuz")


def catalog_id(id: str) -> EntityId:
    return EntityId(id=id, type=EntityType.CATALOG, source="qobuz")


class LastUpdate(BaseModel):
    favorite_tracks_ts: int = 0
    favorite_albums_ts: int = 0
    favorite_artists_ts: int = 0
    favorite_playlists_ts: int = 0


class QobuzClient:
    def __init__(self, email, pwd, app_id, secrets):
        logger.info(f"Logging...")
        self.secrets = secrets
        self.id = str(app_id)
        self.session = httpx.Client(
            transport=RetryTransport(
                read_retries=3, retries=3, http2=True, http1=False
            ),
            timeout=5,
        )
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
                "X-App-Id": self.id,
            }
        )

        self.base = "https://www.qobuz.com/api.json/0.2/"
        self.sec = None
        # a short living cache to be used for reporting purposes
        self.track_url_response_cache = {}
        self.auth(email, pwd)
        self.cfg_setup()

        self.last_update = LastUpdate()
        self.cached = {"albums": {}, "artists": {}, "tracks": {}, "playlists": {}}

    def auth(self, email, pwd):
        params = {
            "email": email,
            "password": pwd,
            "app_id": self.id,
        }
        r = self.session.get(self.base + "user/login", params=params)
        if r.status_code == 401:
            raise AuthenticationError("Invalid credentials.")
        elif r.status_code == 400:
            raise InvalidAppIdError("Invalid app id.")
        else:
            logger.info("Logged: OK")

        r.raise_for_status()
        usr_info = r.json()

        if not usr_info["user"]["credential"]["parameters"]:
            raise IneligibleError("Free accounts are not eligible to play tracks.")
        self.uat = usr_info["user_auth_token"]
        self.session.headers.update({"X-User-Auth-Token": self.uat})
        self.label = usr_info["user"]["credential"]["parameters"]["short_label"]
        logger.info(f"Membership: {self.label}")
        self.credential_id = usr_info["user"]["credential"]["id"]
        self.user_id = usr_info["user"]["id"]

    def test_secret(self, sec):
        try:
            self.get_track_url(track_id=5966783, fmt_id=5, sec=sec)
            return True
        except InvalidAppSecretError:
            return False

    def cfg_setup(self):
        for secret in self.secrets:
            # Falsy secrets
            if not secret:
                continue

            if self.test_secret(secret):
                self.sec = secret
                break

        if self.sec is None:
            raise InvalidAppSecretError("Can't find any valid app secret.")

    def get_track_url(self, track_id, fmt_id=5, sec=None):
        epoint = "track/getFileUrl"
        unix = time.time()
        if int(fmt_id) not in (5, 6, 7, 27):
            raise InvalidQuality("Invalid quality id: choose between 5, 6, 7 or 27")
        r_sig = "trackgetFileUrlformat_id{}intentstreamtrack_id{}{}{}".format(
            fmt_id, track_id, unix, self.sec if sec is None else sec
        )
        r_sig_hashed = hashlib.md5(r_sig.encode("utf-8")).hexdigest()
        params = {
            "request_ts": unix,
            "request_sig": r_sig_hashed,
            "track_id": track_id,
            "format_id": fmt_id,
            "intent": "stream",
        }

        r = None
        for _ in range(3):
            try:
                r = self.session.get(self.base + epoint, params=params)
                break
            except ConnectionError as e:
                logger.error(f"Connection error, retrying {_}: {e}")

        if r is None:
            raise ConnectionError("Failed to get a response after retries.")

        if r.status_code == 400:
            raise InvalidAppSecretError(f"Invalid app secret: {r.json()}.")

        r.raise_for_status()
        self.track_url_response_cache[str(track_id)] = r.json()
        return r.json()

    def get_track_meta(self, track_id):
        epoint = "track/get"
        params = {"track_id": track_id}
        r = self.session.get(self.base + epoint, params=params)
        if r.status_code == 400:
            raise InvalidAppSecretError(f"Invalid app secret: {r.json()}.")

        r.raise_for_status()
        return r.json()

    def get_tracks_meta(self, track_ids: list[int]):
        epoint = "track/getList"
        params = {"tracks_id": track_ids}
        r = self.session.post(self.base + epoint, json=params)

        if r.status_code == 400:
            raise InvalidAppSecretError(f"Invalid app secret: {r.json()}.")

        r.raise_for_status()
        return r.json()["tracks"]["items"]

    def get_qobuz_last_update(self):
        r = self.session.get(self.base + "user/lastUpdate")
        r.raise_for_status()

        return r.json()["last_update"]

    def get_user_playlists(self, offset: int = 0, limit: int = 50, owner_id=None):
        r = self.session.get(
            self.base + "playlist/getUserPlaylists",
            params={"offset": offset, "limit": limit},
        )

        r.raise_for_status()

        cached = {"playlists": r.json()["playlists"]}

        if owner_id is not None:
            filtered_playlists = [
                playlist
                for playlist in cached["playlists"]["items"]
                if playlist["owner"]["id"] == owner_id
            ]
        else:
            filtered_playlists = cached["playlists"]["items"]

        playlists = {
            "offset": offset,
            "limit": limit,
            "total": len(filtered_playlists),
            "items": filtered_playlists[offset : offset + limit],
        }

        return {"playlists": playlists}

    def get_user_favorites(self, type: SearchType, offset: int = 0, limit: int = 50):
        last_update = self.get_qobuz_last_update()[
            {
                SearchType.album: "favorite_album",
                SearchType.artist: "favorite_artist",
                SearchType.track: "favorite_track",
            }[type]
        ]

        type_name = f"{type.name}s"

        if last_update != getattr(self.last_update, f"favorite_{type_name}_ts"):
            logger.info(f"Updating user favorites cache for {type_name}")
            r = self.session.get(
                self.base + "favorite/getUserFavorites",
                params={"type": type.value + "s", "offset": 0, "limit": 500},
            )

            r.raise_for_status()

            self.cached[type_name] = copy.deepcopy(r.json()[type_name])

            setattr(self.last_update, f"favorite_{type_name}_ts", last_update)

        retval = {
            "offset": offset,
            "limit": limit,
            "total": len(self.cached[type_name]["items"]),
            "items": self.cached[type_name]["items"][offset : offset + limit],
        }

        return {type_name: retval}


def get_client(config: QobuzConfig) -> QobuzClient:
    email = config.email
    password = config.password_hash
    bundle = Bundle()

    app_id = bundle.get_app_id()
    secrets = [secret for secret in bundle.get_secrets().values() if secret]
    client = QobuzClient(email, password, app_id, secrets)
    return client


def qobuz_link_retriever(qobuz_client, id, format_id) -> TrackUrl:
    track = qobuz_client.get_track_url(id, fmt_id=format_id)
    track_url = TrackUrl(url=track["url"], format=track["mime_type"])
    return track_url


def append_str(s1: str, s2: str) -> str:
    if not s2:
        return s1
    else:
        return s1.strip() + f" ({s2.strip()})"


def metadata_from_track(track, album_meta={}):
    album_info = track.get("album", album_meta)
    version = album_info.get("version", None)
    return Track(
        **{
            "id": track_id(str(track["id"])),
            "title": append_str(track["title"], track.get("version", None)),
            "performer": (
                Artist(
                    name=track["performer"]["name"],
                    id=artist_id(str(track["performer"]["id"])),
                )
                if "performer" in track
                else Artist(
                    name=album_info["artist"].get("name", None),
                    id=artist_id(str(album_info["artist"].get("id", None))),
                )
            ),
            "duration": track["duration"],
            "album": Album(
                id=album_id(str(album_info["id"])),
                title=append_str(album_info["title"], version),
                image=album_info["image"],
                label=Label(
                    id=label_id(str(album_info["label"]["id"])),
                    name=album_info["label"]["name"],
                ),
                genre=Genre(
                    id=genre_id(str(album_info["genre"]["id"])),
                    name=album_info["genre"]["name"],
                ),
            ),
            "replaygain_peak": track.get("audio_info", {}).get(
                "replaygain_track_peak", None
            ),
            "replaygain_gain": track.get("audio_info", {}).get(
                "replaygain_track_gain", None
            ),
        }
    )


class QobuzInputModule(InputModule):
    def __init__(
        self,
        config: QobuzConfig,
        qobuz_client: QobuzClient,
        event_emitter: EventEmitterAPI,
    ):
        self.format_id = (5, 6, 7, 27)[
            list(QobuzAudioFormat).index(QobuzAudioFormat(config.format))
        ]
        logger.info(f"Selecting Format '{config.format}', id = {self.format_id}")
        self.qobuz_client = qobuz_client
        self.event_emitter = event_emitter
        self.last_update = LastUpdate()
        self.user_playlist = []

    def module_name(self) -> str:
        return "Qobuz"

    def search(
        self, type: SearchType, query: str, offset=0, limit=50
    ) -> BrowseItemList:
        return self._search_items(type, query, offset, limit)

    def browse(
        self,
        entity_id: EntityId,
        offset: PositiveInt = 0,
        limit: PositiveInt = 50,
        genre_ids: List[EntityId] = [],
    ) -> BrowseItemList:
        if entity_id.type == EntityType.ALBUM:
            return self._browse_album(entity_id.id, offset, limit)
        elif entity_id.type == EntityType.PLAYLIST:
            return self._browse_playlist(entity_id.id, offset, limit)
        elif entity_id.type == EntityType.ARTIST:
            return self._browse_artist(entity_id.id, offset, limit)
        elif entity_id.type == EntityType.CATALOG:
            return self._browse_catalog(
                entity_id.id, offset=offset, limit=limit, genre_ids=genre_ids
            )
        else:
            return EmptyList(offset, limit)

    def _browse_album(
        self, id: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "album/get",
            params={"album_id": id, "offset": offset, "limit": limit},
        )

        if response.is_success != True:
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

    def _browse_playlist(
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

        if response.is_success != True:
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

    def _browse_artist(
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

        if response.is_success != True:
            return EmptyList(offset, limit)

        rjson = response.json()

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=rjson["albums"]["total"],
            items=self._albums_to_browse_category(response.json()["albums"]["items"]),
        )

    def list_favorite(
        self, type: SearchType, filter: str, offset: int = 0, limit: int = 50
    ) -> BrowseItemList:
        if type == SearchType.playlist:
            rjson = self.qobuz_client.get_user_playlists(offset=0, limit=500)
        else:
            rjson = self.qobuz_client.get_user_favorites(type, offset=0, limit=500)

        result = self._format_list_response(rjson, offset, limit)
        filtered_items = [
            item
            for item in result.items
            if (item.name and filter.lower() in item.name.lower())
            or (item.subname and filter.lower() in item.subname.lower())
        ]

        filtered_result = BrowseItemList(
            offset=offset,
            limit=limit,
            total=len(filtered_items),
            items=filtered_items[offset : offset + limit],
        )

        return filtered_result

    def _browse_catalog(
        self,
        endpoint: str,
        offset: int = 0,
        limit: int = 50,
        genre_ids: List[EntityId] = [],
    ) -> BrowseItemList:
        if endpoint == "root":
            all_items = [
                BrowseItem(
                    id=catalog_id("recent-releases"),
                    name="Recent Releases",
                    can_browse=True,
                    can_add=False,
                    catalog=Catalog(
                        id=catalog_id("recent-releases"),
                        title="Recent Releases",
                        can_genre_filter=True,
                        preview_config=Preview(
                            type=PreviewType.CAROUSEL,
                            content_type=PreviewContentType.ALBUM,
                            items_count=5,
                            rows_count=1,
                            aspect_ratio=1.0,
                        ),
                    ),
                ),
                BrowseItem(
                    id=catalog_id("new-releases"),
                    name="New Releases",
                    can_browse=True,
                    can_add=False,
                    catalog=Catalog(
                        id=catalog_id("new-releases"),
                        title="New Releases",
                        can_genre_filter=True,
                        preview_config=Preview(
                            type=PreviewType.IMAGE_TEXT,
                            content_type=PreviewContentType.ALBUM,
                            items_count=20,
                            rows_count=2,
                            aspect_ratio=1.0,
                        ),
                    ),
                ),
                BrowseItem(
                    id=catalog_id("playlist-by-category"),
                    name="Playlist By Category",
                    can_browse=True,
                    can_add=False,
                    catalog=Catalog(
                        id=catalog_id("playlist-by-category"),
                        title="Playlist By Category",
                        can_genre_filter=True,
                        preview_config=Preview(
                            type=PreviewType.TEXT_ONLY,
                            content_type=PreviewContentType.CATALOG,
                            items_count=20,
                            rows_count=2,
                            aspect_ratio=1 / 0.475,
                        ),
                    ),
                ),
                BrowseItem(
                    id=catalog_id("qobuz-playlists"),
                    name="Qobuz Playlists",
                    can_browse=True,
                    can_add=False,
                    catalog=Catalog(
                        id=catalog_id("qobuz-playlists"),
                        title="Qobuz Playlists",
                        can_genre_filter=True,
                        preview_config=Preview(
                            type=PreviewType.IMAGE_TEXT,
                            content_type=PreviewContentType.PLAYLIST,
                            rows_count=2,
                            items_count=20,
                            aspect_ratio=1 / 0.475,
                        ),
                    ),
                ),
                BrowseItem(
                    id=catalog_id("press-awards"),
                    name="Press Awards",
                    can_browse=True,
                    can_add=False,
                    catalog=Catalog(
                        id=catalog_id("press-awards"),
                        title="Press Awards",
                        can_genre_filter=True,
                        preview_config=Preview(
                            type=PreviewType.IMAGE_TEXT,
                            content_type=PreviewContentType.ALBUM,
                            items_count=14,
                            rows_count=1,
                            aspect_ratio=1.0,
                            card_size=CardSize.LARGE,
                        ),
                    ),
                ),
                BrowseItem(
                    id=catalog_id("most-streamed"),
                    name="Most Streamed",
                    can_browse=True,
                    can_add=False,
                    catalog=Catalog(
                        id=catalog_id("most-streamed"),
                        title="Top Releases",
                        can_genre_filter=True,
                        preview_config=Preview(
                            type=PreviewType.IMAGE_TEXT,
                            content_type=PreviewContentType.ALBUM,
                            rows_count=2,
                            aspect_ratio=1.0,
                            items_count=20,
                            card_size=CardSize.SMALL,
                        ),
                    ),
                ),
            ]
            return BrowseItemList(
                offset=offset,
                limit=limit,
                total=len(all_items),
                items=all_items[offset : offset + limit],
            )
        elif endpoint == "recent-releases":
            res = self._get_new_releases(
                "new-releases-full", offset, max(0, min(5 - offset, limit)), genre_ids
            )
            res.total = min(res.total, 5)
            return res
        elif endpoint == "new-releases":
            return self._get_new_releases("new-releases-full", offset, limit, genre_ids)
        elif endpoint == "qobuz-playlists":
            return self._get_qobuz_playlists(offset, limit, genre_ids)
        elif endpoint == "playlist-by-category":
            return self._get_playists_by_category(offset, limit, genre_ids)
        elif endpoint == "press-awards":
            return self._get_new_releases("press-awards", offset, limit, genre_ids)
        elif endpoint == "most-streamed":
            return self._get_new_releases("most-streamed", offset, limit, genre_ids)
        else:
            ep = endpoint.split("_")
            if len(ep) > 1:
                if ep[0] == "playlist-by-category":
                    return self._get_qobuz_playlists(offset, limit, genre_ids, ep[1])
                elif ep[0] == "album-suggestions":
                    return self._suggest_albums_similar_to(ep[1], offset, limit)
                elif ep[0] == "playlist-suggestions":
                    return self._suggest_playlists_similar_to(ep[1], offset, limit)
                elif ep[0] == "similar-artists":
                    return self._suggest_artists_similar_to(ep[1], offset, limit)

        return EmptyList(offset, limit)

    def _get_new_releases(
        self, type: str, offset: int, limit: int, genre_ids: list[EntityId]
    ) -> BrowseItemList:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "/album/getFeatured",
            params={
                "type": type,
                "offset": offset,
                "limit": limit,
                "genre_ids": ",".join([str(genre_id.id) for genre_id in genre_ids]),
            },
        )

        if response.is_success != True:
            return EmptyList(offset, limit)

        rjson = response.json()

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=rjson["albums"]["total"],
            items=self._albums_to_browse_category(response.json()["albums"]["items"]),
        )

    def _get_qobuz_playlists(
        self,
        offset: int,
        limit: int,
        genre_ids: list[EntityId],
        tags: str | None = None,
    ):
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "/playlist/getFeatured",
            params={
                "type": "editor-picks",
                "offset": offset,
                "limit": limit,
                "genre_ids": ",".join([str(genre_id.id) for genre_id in genre_ids]),
                "tags": tags,
            },
        )

        if response.is_success != True:
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

    def _get_playists_by_category(
        self, offset: int, limit: int, genre_ids: List[EntityId]
    ):
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "/playlist/getTags",
            params={
                "offset": offset,
                "limit": limit,
                "genre_ids": ",".join([str(genre_id.id) for genre_id in genre_ids]),
            },
        )

        if response.is_success != True:
            return EmptyList(offset, limit)

        tags = response.json()["tags"]

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=len(tags),
            items=[
                BrowseItem(
                    id=catalog_id("playlist-by-category_" + tags[i]["slug"]),
                    name=json.loads(tags[i]["name_json"])["en"],
                    can_browse=True,
                    can_add=False,
                    catalog=Catalog(
                        id=catalog_id("playlist-by-category_" + tags[i]["slug"]),
                        title=json.loads(tags[i]["name_json"])["en"],
                        can_genre_filter=True,
                        preview_config=Preview(
                            type=PreviewType.IMAGE_TEXT,
                            aspect_ratio=1 / 0.475,
                        ),
                    ),
                )
                for i in range(len(tags))
            ][offset : offset + limit],
        )

    def _get_artist_tracks(self, artist_id: str, offset: int, limit: int):
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "/artist/getTracks",
            params={
                "id": artist_id,
                "offset": offset,
                "limit": limit,
            },
        )

        if response.is_success != True:
            return EmptyList(offset, limit)

        rjson = response.json()

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=rjson["tracks"]["total"],
            items=self._tracks_to_browse_categories(response.json()["tracks"]["items"]),
        )

    def get_track_info(self, track_ids: list[str]) -> list[TrackInfo]:
        if len(track_ids) == 0:
            return []

        all_tracks = []
        chunk_size = 49
        for i in range(0, len(track_ids), chunk_size):
            chunk = track_ids[i : i + chunk_size]
            tracks = self.qobuz_client.get_tracks_meta([int(_) for _ in chunk])
            all_tracks.extend(tracks)
        return [self._track_to_track_info(track) for track in all_tracks]

    def _track_to_track_info(self, track):
        track_info = TrackInfo(
            id=track_id(str(track["id"])),
            link_retriever=partial(
                qobuz_link_retriever, self.qobuz_client, track["id"], self.format_id
            ),
            metadata=metadata_from_track(track),
        )

        return track_info

    def _tracks_to_browse_categories(self, tracks, album_meta={}):
        result = []
        for track in tracks:
            tid = str(track["id"])
            album = track.get("album", album_meta)
            album_version = album.get("version", None)

            browse_item = BrowseItem(
                id=track_id(tid),
                name=append_str(track["title"], track.get("version", None)),
                subname=(
                    track["performer"]["name"]
                    if "performer" in track
                    else album.get("artist", {"name": None})["name"]
                ),
                can_browse=False,
                can_add=True,
                track=Track(
                    id=track_id(tid),
                    title=append_str(track["title"], track.get("version", None)),
                    duration=track["duration"],
                    performer=(
                        Artist(
                            id=artist_id(str(track["performer"]["id"])),
                            name=track["performer"]["name"],
                        )
                        if "performer" in track
                        else None
                    ),
                    album=Album(
                        id=album_id(str(album["id"])),
                        title=append_str(album["title"], album_version),
                        artist=Artist(
                            name=album["artist"]["name"],
                            id=artist_id(str(album["artist"]["id"])),
                        ),
                        image=CoverImage(**album["image"]),
                    ),
                    playlist_track_id=str(track.get("playlist_track_id", None)),
                ),
            )
            if "favorited_at" in track:
                browse_item.timestamp = track["favorited_at"]

            result.append(browse_item)
        return result

    def _search_items(self, item_type, query, offset, limit):
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + item_type.value + "/search",
            params={"query": query, "limit": limit, "offset": offset},
        )

        if response.is_success != True:
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
                id=artist_id(str(artist["id"])),
                name=artist["name"],
                subname=None,
                can_browse=True,
                can_add=False,
                timestamp=artist.get("favorited_at", 0),
                artist=Artist(
                    id=artist_id(str(artist["id"])),
                    name=artist["name"],
                    image=(
                        CoverImage(
                            thumbnail=artist["image"].get("small", None),
                            small=artist["image"].get("medium", None),
                            large=artist["image"].get("large", None),
                        )
                        if artist["image"]
                        else None
                    ),
                    album_count=artist["albums_count"],
                ),
                sections=[
                    BrowseItem(
                        id=artist_id(str(artist["id"])),
                        name="Albums",
                        can_browse=True,
                        can_add=False,
                        catalog=Catalog(
                            id=artist_id(str(artist["id"])),
                            title="Albums",
                            can_genre_filter=False,
                            preview_config=Preview(
                                type=PreviewType.TILE,
                                content_type=PreviewContentType.ALBUM,
                                items_count=10,
                                rows_count=1,
                                aspect_ratio=1.0,
                                card_size=CardSize.SMALL,
                            ),
                        ),
                    ),
                    BrowseItem(
                        id=catalog_id("similar-artists_" + str(artist["id"])),
                        name="Similar artists",
                        can_browse=True,
                        can_add=False,
                        catalog=Catalog(
                            id=catalog_id("similar-artists_" + str(artist["id"])),
                            title="Similar artists",
                            can_genre_filter=False,
                            preview_config=Preview(
                                type=PreviewType.IMAGE_TEXT,
                                content_type=PreviewContentType.ARTIST,
                                items_count=5,
                                rows_count=1,
                                aspect_ratio=1.0,
                                card_size=CardSize.SMALL,
                            ),
                        ),
                    ),
                ],
            )
            for artist in artists
        ]

    def _albums_to_browse_category(self, albums):
        return [
            BrowseItem(
                id=album_id(str(album["id"])),
                name=append_str(album["title"], album.get("version", None)),
                subname=(
                    (artist := self._extract_artist_from_album(album)) and artist.name
                ),
                can_browse=True,
                can_add=True,
                timestamp=album.get("favorited_at", 0),
                album=Album(
                    id=album_id(str(album["id"])),
                    title=append_str(album["title"], album.get("version", None)),
                    artist=artist,
                    image=(
                        CoverImage(
                            thumbnail=album["image"].get("thumbnail", None),
                            small=album["image"].get("small", None),
                            large=album["image"].get("large", None),
                        )
                        if "image" in album and album["image"]
                        else None
                    ),
                    duration=album["duration"],
                    track_count=album.get("track_count", album.get("tracks_count", 0)),
                    genre=Genre(
                        id=genre_id(str(album["genre"]["id"])),
                        name=album["genre"]["name"],
                    ),
                ),
                sections=[
                    BrowseItem(
                        id=album_id(str(album["id"])),
                        name="Tracks",
                        can_browse=True,
                        can_add=True,
                        catalog=Catalog(
                            id=album_id(str(album["id"])),
                            title="Tracks",
                            can_genre_filter=False,
                            preview_config=Preview(
                                type=PreviewType.TILE_NUMBERED,
                                content_type=PreviewContentType.TRACK,
                                items_count=10,
                                rows_count=1,
                                aspect_ratio=1.0,
                                card_size=CardSize.SMALL,
                            ),
                        ),
                    ),
                    *(
                        [
                            BrowseItem(
                                id=artist.id,
                                name="More from this artist",
                                can_browse=True,
                                can_add=False,
                                catalog=Catalog(
                                    id=artist.id,
                                    title="More from this artist",
                                    can_genre_filter=False,
                                    preview_config=Preview(
                                        type=PreviewType.IMAGE_TEXT,
                                        content_type=PreviewContentType.ALBUM,
                                        items_count=10,
                                        rows_count=1,
                                        aspect_ratio=1.0,
                                        card_size=CardSize.SMALL,
                                    ),
                                ),
                            )
                        ]
                        if artist
                        else []
                    ),
                    BrowseItem(
                        id=catalog_id("album-suggestions_" + str(album["id"])),
                        name="You may also like",
                        can_browse=True,
                        can_add=False,
                        catalog=Catalog(
                            id=catalog_id("album-suggestions_" + str(album["id"])),
                            title="You may also like",
                            can_genre_filter=False,
                            preview_config=Preview(
                                type=PreviewType.IMAGE_TEXT,
                                content_type=PreviewContentType.ALBUM,
                                items_count=5,
                                rows_count=1,
                                aspect_ratio=1.0,
                                card_size=CardSize.SMALL,
                            ),
                        ),
                    ),
                ],
            )
            for album in albums
        ]

    def _extract_artist_from_album(self, album) -> Optional[Artist]:
        if "artist" in album:
            return Artist(
                id=artist_id(str(album["artist"]["id"])),
                name=album["artist"]["name"],
            )
        elif "performer" in album:
            return Artist(
                id=artist_id(str(album["performer"]["id"])),
                name=album["performer"]["name"],
            )
        elif "artists" in album:
            return Artist(
                id=artist_id(str(album["artists"][0]["id"])),
                name=album["artists"][0]["name"],
            )
        else:
            return None

    def _playlists_to_browse_category(self, playlists):
        return [
            BrowseItem(
                id=playlist_id(str(playlist["id"])),
                name=playlist["name"],
                subname=playlist["owner"]["name"],
                can_browse=True,
                can_add=True,
                playlist=self._qobuz_playlist_to_playlist(playlist),
                sections=[
                    BrowseItem(
                        id=playlist_id(str(playlist["id"])),
                        name="Tracks",
                        can_browse=True,
                        can_add=True,
                        catalog=Catalog(
                            id=playlist_id(str(playlist["id"])),
                            title="Tracks",
                            can_genre_filter=False,
                            preview_config=Preview(
                                type=PreviewType.TILE,
                                content_type=PreviewContentType.TRACK,
                                items_count=15,
                                rows_count=1,
                                aspect_ratio=1.0,
                                card_size=CardSize.SMALL,
                            ),
                        ),
                    ),
                    BrowseItem(
                        id=catalog_id("playlist-suggestions_" + str(playlist["id"])),
                        name="Similar playlists",
                        can_browse=True,
                        can_add=False,
                        catalog=Catalog(
                            id=catalog_id(
                                "playlist-suggestions_" + str(playlist["id"])
                            ),
                            title="Similar playlists",
                            can_genre_filter=False,
                            preview_config=Preview(
                                type=PreviewType.IMAGE_TEXT,
                                content_type=PreviewContentType.PLAYLIST,
                                items_count=9,
                                rows_count=1,
                                aspect_ratio=1 / 0.475,
                                card_size=CardSize.SMALL,
                            ),
                        ),
                    ),
                ],
            )
            for playlist in playlists
        ]

    def _qobuz_playlist_to_playlist(self, playlist):
        images = playlist.get("images", [None])
        images150 = playlist.get("images150", [None])
        images300 = playlist.get("images300", [None])
        image_rectangle = playlist.get("image_rectangle", images300)
        image_rectangle_mini = playlist.get("image_rectangle_mini", images)

        return Playlist(
            id=playlist_id(str(playlist["id"])),
            name=playlist["name"],
            owner=Owner(
                name=playlist["owner"]["name"],
                id=user_id(str(playlist["owner"]["id"])),
            ),
            image=CoverImage(
                small=images150[0] if images150 else None,
                large=image_rectangle[0] if image_rectangle else None,
                thumbnail=image_rectangle_mini[0] if image_rectangle_mini else None,
            ),
            description=playlist["description"],
            track_count=playlist["tracks_count"],
        )

    def get_favorite_ids(self) -> FavoriteIds:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "favorite/getUserFavoriteIds",
            params={"limit": 5000},
        )

        if response.is_success != True:
            return FavoriteIds()

        rjson = response.json()

        return FavoriteIds(
            albums=[album_id(str(id)) for id in rjson["albums"]],
            artists=[artist_id(str(id)) for id in rjson["artists"]],
            tracks=[track_id(str(id)) for id in rjson["tracks"]],
            playlists=self._get_favorite_playlist_ids(),
        )

    def _get_favorite_playlist_ids(self) -> list[EntityId]:
        rjson = self.qobuz_client.get_user_playlists(limit=500)

        return [
            playlist_id(str(playlist["id"])) for playlist in rjson["playlists"]["items"]
        ]

    def add_to_favorite(self, id: str):
        entity_id = EntityId.from_string(id)

        if entity_id.type == EntityType.PLAYLIST:
            endpoint = "playlist/subscribe"
            params = {"playlist_id": entity_id.id}
        else:
            endpoint = "favorite/create"
            params = {entity_id.type.value + "_ids": entity_id.id}

        response = self.qobuz_client.session.post(
            self.qobuz_client.base + endpoint, params=params
        )

        response.raise_for_status()

        rjson = response.json()

        logger.info(f"Add to favorite response: {rjson}")

        if "status" not in rjson or rjson["status"] != "success":
            raise Exception(f"Failed to add to favorite: {response.text}")

        self.event_emitter.dispatch(
            FavoriteAddedEvent(id=entity_id),
        )

    def remove_from_favorite(self, id: str):
        entity_id = EntityId.from_string(id)

        if entity_id.type == EntityType.PLAYLIST:
            endpoint = "playlist/unsubscribe"
            params = {"playlist_id": entity_id.id}
        else:
            endpoint = "favorite/delete"
            params = {entity_id.type.value + "_ids": entity_id.id}

        response = self.qobuz_client.session.post(
            self.qobuz_client.base + endpoint, params=params
        )

        response.raise_for_status()

        rjson = response.json()

        if "status" not in rjson or rjson["status"] != "success":
            raise Exception(f"Failed to remove from favorite: {response.text}")

        self.event_emitter.dispatch(
            FavoriteRemovedEvent(id=entity_id),
        )

    def list_genre(self, offset: int, limit: int) -> GenreList:
        endpoint = "genre/list"
        response = self.qobuz_client.session.get(self.qobuz_client.base + endpoint)

        response.raise_for_status()

        rjson = response.json()

        return GenreList(
            offset=offset,
            limit=limit,
            total=rjson["genres"]["total"],
            items=[
                Genre(
                    id=genre_id(str(genre["id"])),
                    name=genre["name"],
                )
                for genre in rjson["genres"]["items"]
            ],
        )

    def get(self, entity_id: EntityId) -> BrowseItem:
        if entity_id.type == EntityType.ALBUM:
            return self._album_get(entity_id.id)
        elif entity_id.type == EntityType.PLAYLIST:
            return self._playlist_get(entity_id.id)
        elif entity_id.type == EntityType.ARTIST:
            return self._artist_get(entity_id.id)
        elif entity_id.type == EntityType.TRACK:
            return self._track_get(entity_id.id)
        else:
            raise ValueError(f"Unsupported EntityId type: {entity_id.type.name}")

    def _album_get(self, id: str) -> BrowseItem:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "album/get",
            params={"album_id": id, "offset": 0, "limit": 0},
        )

        response.raise_for_status()

        rjson = response.json()

        return self._albums_to_browse_category([rjson])[0]

    def _playlist_get(self, id: str) -> BrowseItem:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "playlist/get",
            params={
                "playlist_id": id,
                "offset": 0,
                "limit": 0,
            },
        )

        response.raise_for_status()

        rjson = response.json()

        return self._playlists_to_browse_category([rjson])[0]

    def _artist_get(self, id: str) -> BrowseItem:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "artist/get",
            params={
                "artist_id": id,
            },
        )

        response.raise_for_status()

        rjson = response.json()

        return self._artists_to_browse_category([rjson])[0]

    def _track_get(self, id: str) -> BrowseItem:
        rjson = self.qobuz_client.get_track_meta(id)
        return self._tracks_to_browse_categories([rjson])[0]

    def _to_playlist_response(self, obj):
        return Playlist(
            id=playlist_id(str(obj["id"])),
            name=obj["name"],
            description=obj["description"],
            track_count=obj["tracks_count"],
            owner=Owner(
                name=obj["owner"]["name"],
                id=user_id(str(obj["owner"]["id"])),
            ),
        )

    def playlist_user_list(self, offset: int = 0, limit: int = 25) -> BrowseItemList:
        rjson = self.qobuz_client.get_user_playlists(
            offset=offset, limit=limit, owner_id=self.qobuz_client.user_id
        )

        return self._format_list_response(rjson, offset, limit)

    def playlist_create(self, name, description) -> Playlist:
        response = self.qobuz_client.session.post(
            self.qobuz_client.base + "playlist/create",
            params={"name": name, "description": description},
        )

        response.raise_for_status()

        rjson = response.json()

        return self._to_playlist_response(rjson)

    def playlist_update(self, id, name, description) -> Playlist:
        response = self.qobuz_client.session.post(
            self.qobuz_client.base + "playlist/update",
            params={
                "playlist_id": EntityId.from_string(id).id,
                "name": name,
                "description": description,
            },
        )

        response.raise_for_status()

        rjson = response.json()

        return self._to_playlist_response(rjson)

    def playlist_delete(self, id):
        response = self.qobuz_client.session.post(
            self.qobuz_client.base + "playlist/delete",
            params={"playlist_id": EntityId.from_string(id).id},
        )

        response.raise_for_status()

        return response.json()

    def playlist_add_tracks(
        self, id: str, track_ids: List[str], allow_duplicates: bool
    ) -> Playlist:
        response = self.qobuz_client.session.post(
            self.qobuz_client.base + "playlist/addTracks",
            params={
                "no_duplicate": not allow_duplicates,
                "playlist_id": EntityId.from_string(id).id,
                "track_ids": ",".join(
                    (EntityId.from_string(track_id).id for track_id in track_ids)
                ),
            },
        )

        response.raise_for_status()
        rjson = response.json()

        return self._to_playlist_response(rjson)

    def playlist_remove_tracks(self, id, playlist_track_ids):
        response = self.qobuz_client.session.post(
            self.qobuz_client.base + "playlist/deleteTracks",
            params={
                "playlist_id": EntityId.from_string(id).id,
                "playlist_track_ids": ",".join(playlist_track_ids),
            },
        )

        response.raise_for_status()
        rjson = response.json()

        return self._to_playlist_response(rjson)

    def _suggest_albums_similar_to(
        self, id: str, offset: int = 0, limit: int = 25
    ) -> BrowseItemList:
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "album/suggest",
            params={"album_id": id},
        )

        if response.is_success != True:
            return EmptyList(offset, limit)

        rjson = response.json()
        albums = rjson["albums"]["items"][offset : offset + limit]

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=int(rjson["albums"]["limit"]),
            items=self._albums_to_browse_category(albums),
        )

    def _suggest_playlists_similar_to(self, id: str, offset: int, limit: int = 10):
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "playlist/get",
            params={"playlist_id": id, "extra": "getSimilarPlaylists"},
        )

        if response.is_success != True:
            return EmptyList(offset, limit)

        rjson = response.json()
        playlists = rjson["similarPlaylist"]["items"][offset : offset + limit]

        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=len(rjson["similarPlaylist"]["items"]),
            items=self._playlists_to_browse_category(playlists),
        )

    def _suggest_artists_similar_to(self, id: str, offset: int, limit: int = 10):
        response = self.qobuz_client.session.get(
            self.qobuz_client.base + "artist/getSimilarArtists",
            params={"artist_id": id, "offset": offset, "limit": limit},
        )
        if response.is_success != True:
            return EmptyList(offset, limit)
        rjson = response.json()
        artists = rjson["artists"]["items"]
        return BrowseItemList(
            offset=offset,
            limit=limit,
            total=rjson["artists"]["total"],
            items=self._artists_to_browse_category(artists),
        )

    def get_resource_path(self, id) -> str | None:
        return None
