from contextlib import asynccontextmanager
import mimetypes
from pathlib import Path
from typing import List, Optional, Union
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from data_model.response_model import FavoriteIds, GenreList, PlaybackMode, PlayerState
from src import config, state_keeper
from src.ext_device import Volume
from src.player_setup import setup, shutdown
from src.rest_event_proxy import EventStream

import logging
import json

from src.inputmodule import SearchType
from src.service_discovery import ServiceDiscovery

from src.config import Config


@asynccontextmanager
async def lifespan(app: FastAPI):
    sd = None
    try:
        sd = ServiceDiscovery(app.state.config)
        await sd.register_service()
        state_keeper.restore_state(app.state.playqueue, app.state.inputmodule)
        yield

    except Exception as e:
        logger.error(f"Error: {e}")

    finally:
        logger.info("Shutting down...")
        if sd is not None:
            await sd.unregister_service()
        shutdown()
        app.state.event_listener.terminate()
        state_keeper.save_state(app.state.playqueue, app.state.inputmodule)
        app.state.config.save()
        app.state.playqueue.terminate()


logger = logging.getLogger(__name__.split(".")[-1])


def create_app(config: Config):
    app = FastAPI(lifespan=lifespan)
    app.state.config = config
    playqueue, event_listener, inputmodule, device = setup(config)
    app.state.playqueue = playqueue
    app.state.event_listener = event_listener
    app.state.inputmodule = inputmodule
    app.state.device = device

    if not inputmodule:
        raise Exception("No input module configured")

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
            track.id
            for track in inputmodule.browse_playlist(entity_id, limit=5000).items
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

    @app.put("/queue/current_track/seek")
    async def read_queue_seek(position_ms: int):
        value = playqueue.seek(position_ms).get()
        return {"message": "Ok", "position_ms": value}

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

    @app.get("/browse/catalog/{endpoint:path}")
    def browse_catalog(
        endpoint: str = "",
        offset: int = 0,
        limit: int = 10,
        genre_ids: List[int] = Query([]),
    ):
        return inputmodule.browse_catalog(
            endpoint, offset, limit, genre_ids
        ).model_dump(exclude_unset=True)

    @app.get("/search/{search_type}/{query}")
    async def search(
        search_type: SearchType, query: str, offset: int = 0, limit: int = 10
    ):
        return inputmodule.search(search_type, query, offset, limit).model_dump(
            exclude_unset=True
        )

    @app.get("/queue/events")
    async def stream(request: Request):
        async def process_events():
            event_stream = EventStream(event_listener)
            try:
                playqueue.replay()
                while True:
                    if await request.is_disconnected():
                        break
                    event = await run_in_threadpool(event_stream.get_event)
                    if event is not None:
                        yield json.dumps(event) + "\n"
            except Exception as e:
                logger.error(f"Error processing events: {e}")
                yield json.dumps({"error": str(e)}) + "\n"
            finally:
                event_stream.close()

        return StreamingResponse(process_events(), media_type="text/event-stream")

    @app.get("/queue/state")
    async def state() -> PlayerState:
        return playqueue.get_state()

    @app.get("/queue/mode")
    async def mode():
        return playqueue.get_playback_mode()

    @app.put("/queue/mode")
    async def set_mode(
        shuffle: Optional[bool] = None,
        repeat_single: Optional[bool] = None,
        repeat_all: Optional[bool] = None,
    ):
        playqueue.set_playback_mode(shuffle, repeat_single, repeat_all)
        return {"message": "Ok"}

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
        return device.supported_functions() if device else []

    @app.get("/device/get_volume")
    def get_volume(device_id: str) -> Volume:
        return device.get_volume() if device else Volume(current_volume=0, max_volume=0)

    @app.put("/device/set_volume")
    def set_volume(device_id: str, volume: int):
        if device is None:
            return {"message": "No device configured"}

        device.set_volume(volume)
        return {"message": "Ok"}

    @app.get("/favorite/list/{type}")
    def list_favorite(type: SearchType, filter: str, offset: int = 0, limit: int = 10):
        return inputmodule.list_favorite(type, filter, offset, limit).model_dump(
            exclude_unset=True
        )

    @app.put("/favorite/add/{type}/{id}")
    def add_favorite(type: SearchType, id: str):
        return inputmodule.add_to_favorite(type, id)

    @app.delete("/favorite/remove/{type}/{id}")
    def remove_favorite(type: SearchType, id: str):
        return inputmodule.remove_from_favorite(type, id)

    @app.get("/favorite/ids")
    def get_favorite_ids() -> FavoriteIds:
        return inputmodule.get_favorite_ids()

    @app.get("/genre/list")
    def list_genre(offset: int = 0, limit: int = 25) -> GenreList:
        return inputmodule.list_genre(offset=offset, limit=limit)

    @app.get("/get/album/{entity_id}")
    def album_get(entity_id: str):
        return inputmodule.album_get(entity_id).model_dump(exclude_unset=True)

    @app.get("/get/artist/{entity_id}")
    def artist_get(entity_id: str):
        return inputmodule.artist_get(entity_id).model_dump(exclude_unset=True)

    @app.get("/get/track/{entity_id}")
    def track_get(entity_id: str):
        return inputmodule.track_get(entity_id).model_dump(exclude_unset=True)

    @app.get("/get/playlist/{entity_id}")
    def playlist_get(entity_id: str):
        return inputmodule.playlist_get(entity_id).model_dump(exclude_unset=True)

    @app.post("/playlist/create")
    def playlist_create(name: str, description: str):
        return inputmodule.playlist_create(name, description).model_dump(
            exclude_unset=True
        )

    @app.put("/playlist/update")
    def playlist_update(
        playlist_id: str, name: Optional[str], description: Optional[str]
    ):
        return inputmodule.playlist_update(playlist_id, name, description).model_dump(
            exclude_unset=True
        )

    @app.delete("/playlist/delete")
    def playlist_delete(playlist_id: str):
        inputmodule.playlist_delete(playlist_id)
        return {"message": "Ok"}

    @app.post("/playlist/add_tracks")
    def playlist_add_tracks(
        playlist_id: str, track_ids: List[str], allow_duplicates: bool = True
    ):
        return inputmodule.playlist_add_tracks(
            playlist_id, track_ids, allow_duplicates
        ).model_dump(exclude_unset=True)

    @app.get("/playlist/list")
    def playlist_user_list(offset: int = 0, limit: int = 25):
        return inputmodule.playlist_user_list(offset, limit).model_dump(
            exclude_unset=True
        )

    @app.delete("/playlist/remove_tracks")
    def playlist_remove_tracks(playlist_id: str, playlist_track_ids: List[str]):
        return inputmodule.playlist_remove_tracks(
            playlist_id, playlist_track_ids
        ).model_dump(exclude_unset=True)

    @app.get("/server/config")
    def get_config():
        return app.state.config.get_full_config()

    @app.put("/server/restart")
    def restart_server():
        app.state.server.should_exit = True
        return {"message": "Ok"}

    @app.put("/server/config")
    def set_config(key, value):
        app.state.config[key] = value
        return {"message": "Ok"}

    @app.get("/resource/{file_name:path}")
    async def get_resource(file_name: str):
        file_path = Path(inputmodule.get_resource_path(file_name)).resolve()
        logger.info(f"File path: {file_path}")
        if not file_path or not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        mime_type, _ = mimetypes.guess_type(str(file_path))

        return FileResponse(file_path, media_type=mime_type)

    return app
