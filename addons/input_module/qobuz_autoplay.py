from functools import partial
import logging
from qobuz_dl.qopy import Client

from src.playqueue import PlayQueue
from .qobuz_helper import get_track_url, metadata_from_track
from src.inputmodule import InputModule, TrackInfo

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
            link_retriever=partial(get_track_url, self.qobuz_client, track["id"]),
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

        logging.info(
            "Retrieved "
            + str(len(track_ids))
            + " new recommendation(s) using algorithm "
            + req.json()["algorithm"]
        )

        self.remaining_tracks = track_ids

        self.can_request_new = False
