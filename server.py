#!/usr/bin/env python3

import asyncio
from typing import Union
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse
from src.player_setup import setup
from src.rest_event_proxy import EventStream
import logging
import json

app = FastAPI()
playqueue, event_listener, trackbrowser = setup()
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


@app.get("/queue/add/{type}/{entity_id}")
def add_to_queue(type: str, entity_id: str):
    if type not in ["album", "playlist", "track"]:
        return {"error": "Invalid entity type"}
    if type == "track":
        playqueue.add(trackbrowser.get_track_info([entity_id]))
        return {"message": "Ok"}

    tracks = [track.id for track in trackbrowser.browse(type + "/" + entity_id)]
    playqueue.add(trackbrowser.get_track_info(tracks))
    return {"message": "Ok"}


@app.get("/queue/add/album/{album_id}")
def add_album_to_queue(album_id: str, replace: bool = False):
    tracks = [track.id for track in trackbrowser.browse("album/" + album_id)]
    playqueue.add(trackbrowser.get_track_info(tracks), replace=replace)
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
async def browse_favorites(type: str, offset: int = 0, limit: int = 10):
    if type not in ["albums", "tracks"]:
        return {"message", "The endpoint must be albums or tracks"}
    return trackbrowser.browse(f"favorite/{type}", offset, limit)


@app.get("/browse/{type}/{entity_id}")
def browse(type: str, entity_id: str, offset: int = 0, limit: int = 10):
    if type not in ["album", "playlist"]:
        return {"error": "Invalid browse type"}
    return trackbrowser.browse(type + "/" + entity_id, offset, limit)


@app.get("/browse")
async def browse_root(offset: int = 0, limit: int = 10):
    return trackbrowser.browse("", offset, limit)


@app.get("/browse/{toplevel_item}")
async def browse_root(toplevel_item: str, offset: int = 0, limit: int = 10):
    return trackbrowser.browse(toplevel_item, offset, limit)


@app.get("/search/{search_type}/{query}")
async def search(search_type: str, query: str, offset: int = 0, limit: int = 10):
    if search_type not in ["album", "track", "playlist"]:
        return {"error": "Invalid search type"}
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
