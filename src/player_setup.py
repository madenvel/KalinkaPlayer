from functools import partial
import logging
import time
from src.rpiasync import EventEmitter, EventListener
from queue import Queue
from src.qobuz_autoplay import QobuzAutoplay
from src.qobuz_helper import QobuzTrackBrowser, get_client

from src.playqueue import EventType, PlayQueue


def setup_autoplay(client, playqueue: PlayQueue, event_listener: EventListener):
    autoplay = QobuzAutoplay(client, playqueue)
    event_listener.subscribe(
        EventType.RequestMoreTracks, partial(autoplay.add_recommendations, 1)
    )
    event_listener.subscribe(EventType.TracksAdded, autoplay.add_tracks)
    event_listener.subscribe(EventType.TracksRemoved, autoplay.remove_tracks)


def setup():
    client = get_client()
    trackbrowser = QobuzTrackBrowser(client)
    queue = Queue()
    event_emitter = EventEmitter(queue)
    event_listener = EventListener(queue)
    playqueue = PlayQueue(event_emitter)
    setup_autoplay(client, playqueue, event_listener)

    return playqueue, event_listener, trackbrowser
