from contextlib import asynccontextmanager
from typing import List, Union
from fastapi import FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from data_model.response_model import FavoriteIds, GenreList, PlayerState
from src.ext_device import Volume
from src.player_setup import setup
from src.rest_event_proxy import EventStream

import logging
import json

from src.inputmodule import SearchType
from src.service_discovery import ServiceDiscovery

playqueue, event_listener, inputmodule, device = setup()


@asynccontextmanager
async def lifespan(app: FastAPI):
    sd = ServiceDiscovery()
    await sd.register_service()
    yield
    logger.info("Shutting down...")
    await sd.unregister_service()
    event_listener.terminate()
    playqueue.terminate()


logger = logging.getLogger(__name__)
app = FastAPI(lifespan=lifespan)


@app.get("/queue/list")
def read_queue_list(offset: int = 0, limit: int = 10):
    return playqueue.list(offset=offset, limit=limit)


@app.post("/queue/add/tracks")
def add_tracks_to_queue(items: list[str]):
    playqueue.add(inputmodule.get_track_info(items))
    return {"message": "Ok"}


@app.post("/queue/add/track/{entity_id}")
def add_track_to_queue(entity_id: str):
    playqueue.add(inputmodule.get_track_info([entity_id]))
    return {"message": "Ok"}


@app.post("/queue/add/album/{entity_id}")
def add_album_to_queue(entity_id: str):
    tracks = [
        track.id for track in inputmodule.browse_album(entity_id, limit=500).items
    ]
    playqueue.add(inputmodule.get_track_info(tracks))
    return {"message": "Ok"}


@app.post("/queue/add/playlist/{entity_id}")
def add_playlist_to_queue(entity_id: str):
    tracks = [
        track.id for track in inputmodule.browse_playlist(entity_id, limit=5000).items
    ]
    playqueue.add(inputmodule.get_track_info(tracks))
    return {"message": "Ok"}


@app.post("/queue/add/catalog/{entity_id}")
def add_catalog_entry_to_queue(entity_id: str):
    tracks = [
        track.id
        for track in inputmodule.browse_catalog(entity_id, limit=5000).items
        if track.can_add is True
    ]
    playqueue.add(inputmodule.get_track_info(tracks))
    return {"message": "Ok"}


@app.put("/queue/play")
async def queue_play(index: Union[int, None] = None):
    playqueue.play(index)
    return {"message": "Ok"}


@app.put("/queue/pause")
async def queue_pause(paused: bool = True):
    playqueue.pause(paused)
    return {"message": "Ok"}


@app.put("/queue/next")
async def read_queue_next():
    playqueue.next()
    return {"message": "Ok"}


@app.put("/queue/prev")
async def read_queue_prev():
    playqueue.prev()
    return {"message": "Ok"}


@app.put("/queue/stop")
async def read_queue_stop():
    playqueue.stop()
    return {"message": "Ok"}


@app.get("/browse/album/{entity_id}")
def browse_album(entity_id: str, offset: int = 0, limit: int = 10):
    return inputmodule.browse_album(entity_id, offset, limit).model_dump(
        exclude_unset=True
    )


@app.get("/browse/playlist/{entity_id}")
def browse_playlist(entity_id: str, offset: int = 0, limit: int = 10):
    return inputmodule.browse_playlist(entity_id, offset, limit).model_dump(
        exclude_unset=True
    )


@app.get("/browse/artist/{entity_id}")
def browse_artist(entity_id: str, offset: int = 0, limit: int = 10):
    return inputmodule.browse_artist(entity_id, offset, limit).model_dump(
        exclude_unset=True
    )


@app.get("/browse/catalog")
def browse_catalog(offset: int = 0, limit: int = 10):
    return inputmodule.browse_catalog("", offset, limit).model_dump(exclude_unset=True)


@app.get("/browse/catalog/{endpoint:path}")
def browse_catalog(
    endpoint: str, offset: int = 0, limit: int = 10, genre_ids: List[int] = Query([])
):
    return inputmodule.browse_catalog(endpoint, offset, limit, genre_ids).model_dump(
        exclude_unset=True
    )


@app.get("/search/{search_type}/{query}")
async def search(search_type: SearchType, query: str, offset: int = 0, limit: int = 10):
    return inputmodule.search(search_type, query, offset, limit).model_dump(
        exclude_unset=True
    )


@app.get("/queue/events")
async def stream(request: Request):
    async def process_events():
        event_stream = EventStream(event_listener)
        playqueue.replay()
        while True:
            if await request.is_disconnected():
                break
            event = await run_in_threadpool(event_stream.get_event)
            if event is not None:
                yield json.dumps(event) + "\n"

        event_stream.close()

    return StreamingResponse(process_events(), media_type="text/event-stream")


@app.get("/queue/state")
async def state() -> PlayerState:
    return playqueue.get_state()


@app.put("/queue/clear")
async def clear():
    playqueue.clear()
    return {"message": "Ok"}


@app.post("/queue/remove")
async def remove(index: int):
    playqueue.remove([index])
    return {"message": "Ok"}


@app.get("/device/list")
async def set_device_params():
    return device.supported_functions()


@app.get("/device/get_volume")
async def get_volume(device_id: str) -> Volume:
    return device.get_volume()


@app.put("/device/set_volume")
async def set_volume(device_id: str, volume: int):
    device.set_volume(volume)
    return {"message": "Ok"}


@app.get("/favorite/list/{type}")
async def list_favorite(type: SearchType, offset: int = 0, limit: int = 10):
    return inputmodule.list_favorite(type, offset, limit).model_dump(exclude_unset=True)


@app.put("/favorite/add/{type}/{id}")
async def add_favorite(type: SearchType, id: str):
    return inputmodule.add_to_favorite(type, id)


@app.delete("/favorite/remove/{type}/{id}")
async def remove_favorite(type: SearchType, id: str):
    return inputmodule.remove_from_favorite(type, id)


@app.get("/favorite/ids")
async def get_favorite_ids() -> FavoriteIds:
    return inputmodule.get_favorite_ids()


@app.get("/genre/list")
async def list_genre(offset: int = 0, limit: int = 25) -> GenreList:
    return inputmodule.list_genre(offset=offset, limit=limit)


@app.get("/get/album/{entity_id}")
async def album_get(entity_id: str):
    return inputmodule.album_get(entity_id).model_dump(exclude_unset=True)


@app.get("/get/artist/{entity_id}")
async def artist_get(entity_id: str):
    return inputmodule.artist_get(entity_id).model_dump(exclude_unset=True)


@app.get("/get/track/{entity_id}")
async def track_get(entity_id: str):
    return inputmodule.track_get(entity_id).model_dump(exclude_unset=True)


@app.get("/get/playlist/{entity_id}")
async def playlist_get(entity_id: str):
    return inputmodule.playlist_get(entity_id).model_dump(exclude_unset=True)
