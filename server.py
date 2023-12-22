#!/usr/bin/env python3

from typing import Union
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from src.ext_device import Volume
from src.player_setup import setup
from src.rest_event_proxy import EventStream
import logging
import json

from src.trackbrowser import SearchType

app = FastAPI()
playqueue, event_listener, trackbrowser, device = setup()
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
def shutdown():
    logger.info("Shutting down...")
    event_listener.terminate()
    playqueue.terminate()


@app.get("/queue/list")
def read_queue_list(offset: int = 0, limit: int = 10):
    return playqueue.list(offset=offset, limit=limit)


@app.post("/queue/add/tracks")
def add_tracks_to_queue(items: list[str]):
    playqueue.add(trackbrowser.get_track_info(items))
    return {"message": "Ok"}


@app.get("/queue/add/track/{entity_id}")
def add_track_to_queue(entity_id: str):
    playqueue.add(trackbrowser.get_track_info([entity_id]))
    return {"message": "Ok"}


@app.get("/queue/add/album/{entity_id}")
def add_album_to_queue(entity_id: str):
    tracks = [
        track.id for track in trackbrowser.browse_album(entity_id, limit=500).items
    ]
    playqueue.add(trackbrowser.get_track_info(tracks))
    return {"message": "Ok"}


@app.get("/queue/add/playlist/{entity_id}")
def add_playlist_to_queue(entity_id: str):
    tracks = [
        track.id for track in trackbrowser.browse_playlist(entity_id, limit=5000).items
    ]
    playqueue.add(trackbrowser.get_track_info(tracks))
    return {"message": "Ok"}


@app.get("/queue/add/catalog/{entity_id}")
def add_catalog_entry_to_queue(entity_id: str):
    tracks = [
        track.id
        for track in trackbrowser.browse_catalog(entity_id, limit=5000).items
        if track.can_add is True
    ]
    playqueue.add(trackbrowser.get_track_info(tracks))
    return {"message": "Ok"}


@app.get("/queue/play")
async def queue_play(index: Union[int, None] = None):
    playqueue.play(index)
    return {"message": "Ok"}


@app.get("/queue/pause")
async def queue_pause(paused: bool = True):
    playqueue.pause(paused)
    return {"message": "Ok"}


@app.get("/queue/next")
async def read_queue_next():
    playqueue.next()
    return {"message": "Ok"}


@app.get("/queue/prev")
async def read_queue_prev():
    playqueue.prev()
    return {"message": "Ok"}


@app.get("/queue/stop")
async def read_queue_stop():
    playqueue.stop()
    return {"message": "Ok"}


@app.get("/browse/favorite/{type}")
async def browse_favorite(type: SearchType, offset: int = 0, limit: int = 10):
    return trackbrowser.browse_favorite(type, offset, limit)


@app.get("/browse/album/{entity_id}")
def browse_album(entity_id: str, offset: int = 0, limit: int = 10):
    return trackbrowser.browse_album(entity_id, offset, limit)


@app.get("/browse/playlist/{entity_id}")
def browse_playlist(entity_id: str, offset: int = 0, limit: int = 10):
    return trackbrowser.browse_playlist(entity_id, offset, limit)


@app.get("/browse/artist/{entity_id}")
def browse_artist(entity_id: str, offset: int = 0, limit: int = 10):
    return trackbrowser.browse_artist(entity_id, offset, limit)


@app.get("/browse/catalog")
def browse_catalog(offset: int = 0, limit: int = 10):
    return trackbrowser.browse_catalog("", offset, limit)


@app.get("/browse/catalog/{endpoint}")
def browse_catalog(endpoint: str, offset: int = 0, limit: int = 10):
    return trackbrowser.browse_catalog(endpoint, offset, limit)


@app.get("/search/{search_type}/{query}")
async def search(search_type: SearchType, query: str, offset: int = 0, limit: int = 10):
    return trackbrowser.search(search_type, query, offset, limit)


@app.get("/queue/events")
async def stream(request: Request):
    async def process_events():
        event_stream = EventStream(event_listener)
        while True:
            if await request.is_disconnected():
                break
            event = await run_in_threadpool(event_stream.get_event)
            if event is not None:
                yield json.dumps(event) + "\n"

        event_stream.close()

    return StreamingResponse(process_events(), media_type="text/event-stream")


@app.get("/queue/state")
async def state():
    return playqueue.get_state()


@app.get("/queue/clear")
async def clear():
    playqueue.clear()
    return {"message": "Ok"}


@app.get("/queue/remove")
async def remove(index: int):
    playqueue.remove([index])
    return {"message": "Ok"}


@app.get("/device/list")
async def set_device_params():
    return device.supported_functions()


@app.get("/device/get_volume")
async def get_volume(device_id: str) -> Volume:
    return device.get_volume()


@app.get("/device/set_volume")
async def set_volume(device_id: str, volume: int):
    device.set_volume(volume)
    return {"message": "Ok"}
