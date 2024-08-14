from functools import partial
import logging

from src.playqueue import PlayQueue
from .qobuz import QobuzClient, qobuz_link_retriever, metadata_from_track
from src.inputmodule import InputModule, TrackInfo

logger = logging.getLogger(__name__.split(".")[-1])


class QobuzAutoplay:
    def __init__(
        self,
        qobuz_client: QobuzClient,
        playqueue: PlayQueue,
        track_browser: InputModule,
        amount_to_request: int = 50,
    ):
        self.qobuz_client = qobuz_client
        self.playqueue = playqueue
        self.track_browser = track_browser
        self.remaining_tracks: list[TrackInfo] = []
        self.suggested_tracks: set[int] = set()
        self.amount_to_request = amount_to_request
        self.tracks = []
        self.can_request_new = True

    def _track_meta_to_autoplay(self, track):
        return {
            "artist_id": int(track["performer"]["id"]),
            "genre_id": int(track["album"]["genre"]["id"]),
            "label_id": int(track["album"]["label"]["id"]),
            "track_id": int(track["id"]),
        }

    def add_tracks(self, tracks):
        self.tracks.extend(tracks)
        self.can_request_new = True

    def remove_tracks(self, tracks: list[int]):
        for track in tracks:
            del self.tracks[track]

        if not self.tracks:
            self.can_request_new = True
            self.suggested_tracks.clear()
            self.remaining_tracks.clear()

    def add_recommendation(self):
        if not self.remaining_tracks:
            if self.can_request_new is True:
                self._retrieve_new_recommendations()

        if not self.remaining_tracks:
            print("No tracks to recommend")
            return

        recommended_track = self.remaining_tracks.pop(0)
        self.suggested_tracks.add(recommended_track)

        self.playqueue.add(self.track_browser.get_track_info([recommended_track]))

    def _track_to_trackinfo(self, track) -> TrackInfo:
        return TrackInfo(
            metadata=metadata_from_track(track),
            link_retriever=partial(
                qobuz_link_retriever, self.qobuz_client, track["id"]
            ),
        )

    def _retrieve_new_recommendations(self):
        if not self.tracks:
            return

        five_tracks_to_analyse = []

        for i in range(len(self.tracks) - 1, -1, -1):
            if len(five_tracks_to_analyse) == 5:
                break

            if self.tracks[i]["id"] not in self.suggested_tracks:
                five_tracks_to_analyse.append(self.tracks[i])

        tracks_to_analyze = [
            self._track_meta_to_autoplay(track) for track in five_tracks_to_analyse
        ]

        tta_ids = [track["id"] for track in five_tracks_to_analyse]

        listened_tracks = [
            int(track["id"]) for track in self.tracks if track["id"] not in tta_ids
        ]
        params = {
            "limit": self.amount_to_request,
            "listened_tracks_ids": listened_tracks,
            "track_to_analysed": tracks_to_analyze,
        }

        req = self.qobuz_client.session.post(
            self.qobuz_client.base + "dynamic/suggest",
            json=params,
        )

        track_ids = [track["id"] for track in req.json()["tracks"]["items"]]

        logger.info(
            "Retrieved "
            + str(len(track_ids))
            + " new recommendation(s) using algorithm "
            + req.json()["algorithm"]
        )

        self.remaining_tracks = track_ids

        self.can_request_new = False
