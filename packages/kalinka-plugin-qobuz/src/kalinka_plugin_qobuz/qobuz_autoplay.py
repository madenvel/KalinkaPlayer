import logging

from kalinka_plugin_sdk.datamodel import Track
from kalinka_plugin_sdk.api import PlayQueueAPI
from kalinka_plugin_sdk.inputmodule import InputModule
from kalinka_plugin_sdk.events import (
    AnyEventPayload,
    TracksAddedEvent,
    RequestMoreTracksEvent,
    TracksRemovedEvent,
)

from .qobuz import QobuzClient

logger = logging.getLogger(__name__.split(".")[-1])


class QobuzAutoplay:
    def __init__(
        self,
        qobuz_client: QobuzClient,
        playqueue: PlayQueueAPI,
        track_browser: InputModule,
        amount_to_request: int = 50,
    ):
        self.qobuz_client = qobuz_client
        self.playqueue = playqueue
        self.track_browser = track_browser
        self.remaining_tracks: list[str] = []
        self.suggested_tracks: set[str] = set()
        self.amount_to_request = amount_to_request
        self.tracks: list[Track] = []
        self.can_request_new = True

    def _track_meta_to_autoplay(self, track: Track) -> dict:
        return {
            "artist_id": int(track.performer.id.id) if track.performer else None,
            "genre_id": int(track.album.genre.id.id) if track.album.genre else None,
            "label_id": int(track.album.label.id.id) if track.album.label else None,
            "track_id": int(track.id.id) if track.id else None,
        }

    def add_tracks(self, event: AnyEventPayload) -> None:
        if not isinstance(event, TracksAddedEvent):
            logger.warning("Expected TracksAddedEvent, got %s", type(event))
            return

        self.tracks.extend(event.tracks)
        self.can_request_new = True

    def remove_tracks(self, event: AnyEventPayload) -> None:
        if not isinstance(event, TracksRemovedEvent):
            logger.warning("Expected TracksRemovedEvent, got %s", type(event))
            return

        for track in event.indices:
            del self.tracks[track]

        if not self._has_any_qobuz_tracks():
            self.can_request_new = True
            self.suggested_tracks.clear()
            self.remaining_tracks.clear()

    def _has_any_qobuz_tracks(self) -> bool:
        return any(track.id.source == "qobuz" for track in self.tracks)

    def add_recommendation(self, event) -> None:
        if not isinstance(event, RequestMoreTracksEvent):
            logger.warning("Expected RequestMoreTracksEvent, got %s", type(event))
            return

        if not self.remaining_tracks:
            if self.can_request_new:
                self._retrieve_new_recommendations()

        if not self.remaining_tracks:
            logger.info("No tracks to recommend")
            return

        recommended_track = self.remaining_tracks.pop(0)
        self.suggested_tracks.add(recommended_track)
        self.playqueue.add(self.track_browser.get_track_info([recommended_track]))

    def _retrieve_new_recommendations(self) -> None:
        tracks = [track for track in self.tracks if track.id.source == "qobuz"]
        if not tracks:
            logger.debug("No Qobuz tracks available for recommendation")
            return

        five_tracks_to_analyse = []
        for i in range(len(tracks) - 1, -1, -1):
            if len(five_tracks_to_analyse) == 5:
                break
            if tracks[i].id.id not in self.suggested_tracks:
                five_tracks_to_analyse.append(tracks[i])

        tracks_to_analyze = [
            self._track_meta_to_autoplay(track) for track in five_tracks_to_analyse
        ]
        tta_ids = [track.id.id for track in five_tracks_to_analyse]
        listened_tracks = [
            int(track.id.id) for track in tracks if track.id.id not in tta_ids
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
            "Retrieved %d new recommendation(s) using algorithm %s",
            len(track_ids),
            req.json()["algorithm"],
        )

        self.remaining_tracks = track_ids
        self.can_request_new = False
