from functools import partial
import logging
from qobuz_dl.qopy import Client

from src.playqueue import PlayQueue
from src.qobuz_helper import get_track_url, metadata_from_track
from src.trackbrowser import TrackBrowser, TrackInfo

import json

# req = client.session.post(client.base + 'dynamic/suggest', json={
#                           'limit': 15, 'listened_tracks_ids': [], 'track_to_analysed': [{
#                               "track_id": 211741639,
#                               "artist_id": 646014,
#                               "label_id": 310400,
#                               "genre_id": 80
#                           }]})

# {
#     "limit": 50,
#     "listened_tracks_ids": [
#         57751720,
#         822603,
#         39762266,
#         217413992,
#         23432596,
#         2392409,
#         809212,
#         196465022,
#         1049935,
#         2488008,
#         32597024,
#         209185597,
#         125815996,
#         169134411,
#         811011,
#         97459,
#         131297,
#         3441191,
#         4293357,
#         32083483,
#         52542607,
#         91827412,
#         46693505,
#         102292356,
#         186566735
#     ],
#     "track_to_analysed": [
#         {
#             "track_id": 60469414,
#             "artist_id": 4402838,
#             "label_id": 206514,
#             "genre_id": 113
#         },
#         {
#             "track_id": 224182257,
#             "artist_id": 1283440,
#             "label_id": 17426,
#             "genre_id": 5
#         },
#         {
#             "track_id": 38345207,
#             "artist_id": 56669,
#             "label_id": 1363,
#             "genre_id": 3
#         },
#         {
#             "track_id": 56958018,
#             "artist_id": 35381,
#             "label_id": 247705,
#             "genre_id": 80
#         },
#         {
#             "track_id": 2955748,
#             "artist_id": 493813,
#             "label_id": 7470,
#             "genre_id": 117
#         }
#     ]
# }


class QobuzAutoplay:
    def __init__(
        self,
        qobuz_client: Client,
        playqueue: PlayQueue,
        track_browser: TrackBrowser,
        amount_to_request: int = 10,
    ):
        self.qobuz_client = qobuz_client
        self.playqueue = playqueue
        self.track_browser = track_browser
        self.remaining_tracks: list[TrackInfo] = []
        self.listened_tracks = set()
        self.amount_to_request = amount_to_request
        self.tracks_to_analyze = []

    def _track_meta_to_autoplay(self, track):
        return {
            "track_id": track["id"],
            "artist_id": track["album"]["artist"]["id"],
            "label_id": track["album"]["label"]["id"],
            "genre_id": track["album"]["genre"]["id"],
        }

    def add_tracks(self, tracks):
        tracks_to_analyze = [
            self._track_meta_to_autoplay(self.qobuz_client.get_track_meta(track["id"]))
            for track in tracks
            if track["id"] not in self.listened_tracks
        ]
        self.tracks_to_analyze.extend(tracks_to_analyze)

    def remove_tracks(self, tracks: list[int]):
        for track in tracks:
            del self.tracks_to_analyze[track]

    def add_recommendations(self, count=1):
        while len(self.remaining_tracks) < count:
            self._retrieve_new_recommendations()

        (tracks, self.remaining_tracks) = (
            self.remaining_tracks[:count],
            self.remaining_tracks[count:],
        )

        self.playqueue.add(tracks)

    def _track_to_trackinfo(self, track) -> TrackInfo:
        return TrackInfo(
            metadata=metadata_from_track(track),
            link_retriever=partial(get_track_url, self.qobuz_client, track["id"]),
        )

    def _retrieve_new_recommendations(self):
        req = self.qobuz_client.session.post(
            self.qobuz_client.base + "dynamic/suggest",
            json={
                "limit": self.amount_to_request,
                "listened_tracks_ids": list(self.listened_tracks),
                "track_to_analysed": self.tracks_to_analyze,
            },
        )

        track_ids = [track["id"] for track in req.json()["tracks"]["items"]]

        logging.info("Retrieved " + str(len(track_ids)) + " new recommendation(s)")

        self.remaining_tracks.extend(self.track_browser.get_track_info(track_ids))
        self.listened_tracks.update(track_ids)
