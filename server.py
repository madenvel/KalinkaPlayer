#!/usr/bin/env python3

from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse
from src.player_setup import setup
from src.rest_event_proxy import EventStream
from src.events import EventType
import logging

app = FastAPI()
playqueue, event_listener, trackbrowser = setup()
event_stream = EventStream(event_listener)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
def shutdown():
    logger.info("Shutting down...")
    event_listener.terminate()
    playqueue.terminate()


@app.get("/queue/list")
async def read_queue_list(offset: int = 0, limit: int = 10):
    return {"items": playqueue.list(offset=offset, limit=limit)}


@app.post("/queue/add/tracks")
async def add_to_queue(items: list[str]):
    playqueue.add(trackbrowser.get_track_info(items))
    return {"message": "Item added to the queue"}


@app.get("/queue/add/album/{album_id}")
async def add_album_to_queue(album_id: str, replace: bool = False):
    tracks = [track.id for track in trackbrowser.browse("album/" + album_id)]
    playqueue.add(trackbrowser.get_track_info(tracks), replace=replace)
    return {"message": "Item added to the queue"}


@app.get("/queue/play")
async def queue_play(index: int | None = None):
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


@app.get("/browse/{type}/{entity_id}")
async def browse(type: str, entity_id: str, offset: int = 0, limit: int = 10):
    if type not in ["album", "playlist"]:
        return {"error": "Invalid browse type"}
    return trackbrowser.browse(type + "/" + entity_id, offset, limit)


@app.get("/search/{search_type}/{query}")
async def search(search_type: str, query: str, offset: int = 0, limit: int = 10):
    if search_type not in ["album", "track", "playlist"]:
        return {"error": "Invalid search type"}
    return trackbrowser.search(search_type, query, offset, limit)


@app.get("/events/subscribe")
async def subscribe(t: list[EventType] = []):
    sid = event_stream.open_stream(t)
    return {"subscription_id": sid}


@app.get("/events/{stream_id}")
async def stream(stream_id: str, request: Request):
    async def process_events():
        while True:
            if await request.is_disconnected():
                # TODO: clean up the subscription
                break

            message = event_stream.get_event(stream_id)
            if message is None:
                break
            yield message

    return EventSourceResponse(process_events())
