from functools import partial
import logging
import time
from src.rpiasync import EventEmitter, EventListener
from queue import Queue
from addons.music_source.qobuz_autoplay import QobuzAutoplay
from addons.music_source.qobuz_helper import (
    QobuzTrackBrowser,
    get_client,
    get_track_url,
)

from src.playqueue import EventType, PlayQueue
from src.track_url_retriever import TrackUrlRetriever
from src.trackbrowser import SourceType, TrackBrowser
from addons.device.musiccast import Device


def setup_autoplay(
    client,
    playqueue: PlayQueue,
    track_browser: TrackBrowser,
    event_listener: EventListener,
):
    autoplay = QobuzAutoplay(client, playqueue, track_browser)
    event_listener.subscribe(EventType.RequestMoreTracks, autoplay.add_recommendation)
    event_listener.subscribe(EventType.TracksAdded, autoplay.add_tracks)
    event_listener.subscribe(EventType.TracksRemoved, autoplay.remove_tracks)


def setup_device(
    playqueue: PlayQueue, event_listener: EventListener, event_emitter: EventEmitter
):
    device = Device(playqueue, event_emitter)
    event_listener.subscribe(EventType.Playing, device._on_playing)
    event_listener.subscribe(EventType.Paused, device._on_paused_or_stopped)
    event_listener.subscribe(EventType.Stopped, device._on_paused_or_stopped)

    return device


def setup():
    client = get_client()
    trackbrowser = QobuzTrackBrowser(client)
    queue = Queue()
    event_emitter = EventEmitter(queue)
    event_listener = EventListener(queue)
    url_retriever = TrackUrlRetriever()
    url_retriever.register(SourceType.QOBUZ, partial(get_track_url, client))
    playqueue = PlayQueue(event_emitter)
    setup_autoplay(client, playqueue, trackbrowser, event_listener)
    device = setup_device(playqueue, event_listener, event_emitter)

    return playqueue, event_listener, trackbrowser, device
