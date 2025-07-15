from contextlib import asynccontextmanager
import asyncio
import mimetypes
import os
from pathlib import Path
from typing import List, Optional, Union
from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from data_model.datamodel import (
    BrowseItem,
    BrowseItemList,
    Catalog,
    EntityId,
    EntityType,
)
from data_model.response_model import FavoriteIds, GenreList, PlaybackMode, PlayerState
from src import state_keeper
from src.config_model import KalinkaConfig
from src.config_schema_processor import config_to_wire
from src.ext_device import ExternalOutputDevice, Volume
from src.multisearch import multisearch
from src.player_setup import setup, shutdown, modules
from src.rest_event_proxy import EventStream

import logging
import json

from src.inputmodule import InputModule, SearchType, TrackInfo
from src.service_discovery import ServiceDiscovery
from typing import Dict, Any


def save_config(config_file: str, config: KalinkaConfig):
    """Save the configuration to a file."""
    config_data = config.model_dump()
    with open(config_file, "w") as f:
        json.dump(config_data, f, indent=2)
    logger.info(f"Configuration saved to {config_file}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    sd = None
    try:
        sd = ServiceDiscovery(app.state.config)
        await sd.register_service()
        state_keeper.restore_state(app.state.playqueue)
        yield

    except Exception as e:
        logger.error(f"Error: {e}")

    finally:
        logger.info("Shutting down...")
        if sd is not None:
            await sd.unregister_service()
        shutdown(os.path.dirname(app.state.config_file))
        app.state.event_listener.terminate()
        state_keeper.save_state(app.state.playqueue)
        save_config(app.state.config_file, app.state.config)
        app.state.playqueue.terminate()


logger = logging.getLogger(__name__.split(".")[-1])

SourceType = Query(..., description="Name of the input module")


def parse_entity_id(id: str) -> EntityId:
    """Dependency to parse entity ID from string."""
    try:
        return EntityId.from_string(id)
    except (ValueError, Exception) as e:
        logger.error(f"Failed to parse entity ID '{id}': {str(e)}")
        raise HTTPException(
            status_code=400, detail=f"Invalid entity ID format: {str(e)}"
        )


def input_module(name: str) -> InputModule:
    """Get the currently active input module."""
    if not modules.prepared_input_modules:
        raise HTTPException(status_code=500, detail="No input modules available")

    if name not in modules.prepared_input_modules:
        raise HTTPException(status_code=404, detail=f"Input module '{name}' not found")

    interface = modules.prepared_input_modules[name].interface
    if isinstance(interface, InputModule):
        return interface

    raise HTTPException(
        status_code=500,
        detail="Input module interface is not initialized or of wrong type",
    )


def input_module_from_id(entity_id: str | EntityId) -> InputModule:
    """Get the input module based on the entity ID."""
    if isinstance(entity_id, str):
        entityId = EntityId.from_string(entity_id)
    else:
        entityId = entity_id

    source = entityId.source
    if source not in modules.prepared_input_modules:
        raise HTTPException(
            status_code=404, detail=f"Input module '{source}' not found"
        )

    interface = modules.prepared_input_modules[source].interface
    if isinstance(interface, InputModule):
        return interface

    raise HTTPException(
        status_code=500,
        detail="Input module interface is not initialized or of wrong type",
    )


def default_input_module() -> InputModule:
    """Get the default input module."""
    if not modules.prepared_input_modules:
        raise HTTPException(status_code=500, detail="No input modules available")

    default_module = next(iter(modules.prepared_input_modules.values()))
    if isinstance(default_module.interface, InputModule):
        return default_module.interface

    raise HTTPException(
        status_code=500,
        detail="Default input module interface is not initialized or of wrong type",
    )


def create_app(config_file, config: KalinkaConfig):
    app = FastAPI(lifespan=lifespan)
    app.state.config = config
    app.state.config_file = config_file
    playqueue, event_listener = setup(os.path.dirname(config_file), config)
    logger.info("Input modules found: %s", list(modules.prepared_input_modules.keys()))
    app.state.playqueue = playqueue
    app.state.event_listener = event_listener
    prepared_device = next(iter(modules.prepared_devices.values()), None)
    device: Optional[ExternalOutputDevice] = (
        prepared_device.interface
        if prepared_device
        and isinstance(prepared_device.interface, ExternalOutputDevice)
        else None
    )

    @app.get("/queue/list")
    def read_queue_list(offset: int = 0, limit: int = 10):
        return playqueue.list(offset=offset, limit=limit)

    @app.post("/queue/add/items")
    def add_entity_to_queue(ids: list[str]):
        items: list[TrackInfo] = []
        for entity_id in ids:
            entity_id_obj = EntityId.from_string(entity_id)
            module = input_module_from_id(entity_id)
            if entity_id_obj.type != EntityType.TRACK:
                browse_list = module.browse(entity_id_obj, offset=0, limit=5000)
                track_ids = [
                    item.id.id
                    for item in browse_list.items
                    if item.id.type == EntityType.TRACK
                ]
                items.extend(module.get_track_info(track_ids))
            else:
                items.extend(module.get_track_info([entity_id_obj.id]))

        playqueue.add(items)
        return {"message": "Items added to queue", "count": len(items)}

    @app.put("/queue/play")
    async def queue_play(index: Union[int, None] = None):
        playqueue.play(index)
        return {"message": "Ok"}

    @app.put("/queue/pause")
    async def queue_pause(paused: bool = True):
        playqueue.pause(paused)
        return {"message": "Ok"}

    @app.put("/queue/next")
    async def queue_next():
        playqueue.next()
        return {"message": "Ok"}

    @app.put("/queue/prev")
    async def queue_prev():
        playqueue.prev()
        return {"message": "Ok"}

    @app.put("/queue/stop")
    async def queue_stop():
        playqueue.stop()
        return {"message": "Ok"}

    @app.put("/queue/current_track/seek")
    async def queue_seek(position_ms: int):
        value = playqueue.seek(position_ms).get()
        return {"message": "Ok", "position_ms": value}

    @app.get("/browse")
    def browse_root(offset: int = 0, limit: int = 10):
        """Browse the root catalog."""

        result = BrowseItemList(
            offset=offset,
            limit=limit,
            total=len(modules.prepared_input_modules),
            items=[],
        )

        for module_name, module in modules.prepared_input_modules.items():
            if isinstance(module.interface, InputModule):
                entity_id = EntityId(
                    id="root", type=EntityType.CATALOG, source=module_name
                )
                result.items.append(
                    BrowseItem(
                        id=entity_id,
                        name=module.config.name.title() or module_name,
                        url=f"/browse/{entity_id.to_string}",
                        can_browse=True,
                        can_add=False,
                        catalog=Catalog(
                            id=entity_id,
                            title=module.config.name.title() or module_name,
                            image=None,  # Placeholder for catalog image
                            can_genre_filter=False,
                            description="Kalinka Input Module",
                        ),
                    )
                )

        return result.model_dump(exclude_unset=True)

    @app.get("/browse/{id}")
    def browse_entity(
        id: str,
        offset: int = 0,
        limit: int = 10,
        genre_ids: List[str] = Query([]),
    ):
        """Browse an entity by its ID."""
        entity_id = parse_entity_id(id)

        try:
            genre_ids_obj = []
            for genre_id_str in genre_ids:
                genre_id_obj = parse_entity_id(genre_id_str)
                if genre_id_obj.type != EntityType.GENRE:
                    raise ValueError(
                        f"Invalid genre_id type: {genre_id_obj.type}, expected GENRE"
                    )
                genre_ids_obj.append(genre_id_obj)

            input_module = input_module_from_id(entity_id)
            result = input_module.browse(
                entity_id, offset=offset, limit=limit, genre_ids=genre_ids_obj
            )
            return result.model_dump(exclude_unset=True)
        except HTTPException:
            # Re-raise HTTP exceptions as-is
            raise
        except Exception as e:
            logger.error(f"Error browsing entity {id}: {str(e)}")
            raise HTTPException(
                status_code=500, detail=f"Internal server error: {str(e)}"
            )

    @app.get("/search/{search_type}/{query}")
    async def search(
        search_type: SearchType,
        query: str,
        offset: int = 0,
        limit: int = 10,
        source: Optional[str] = None,
    ):
        """Search for items across input modules."""
        sources_split = source.split(",") if source else None
        input_modules: list[InputModule] = [
            module.interface
            for module in modules.prepared_input_modules.values()
            if isinstance(module.interface, InputModule)
        ]

        if sources_split:
            input_modules = list(
                filter(lambda m: m.module_name() in sources_split, input_modules)
            )

        return await multisearch(input_modules, search_type, query, offset, limit)

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
            except asyncio.CancelledError:
                # Handle graceful shutdown - connection was cancelled
                logger.info("Event stream cancelled during server shutdown")
                return
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
    async def device_supported_functions():
        if device is None:
            return {"message": "No device configured"}

        return device.supported_functions()

    @app.get("/device/get_volume")
    def get_volume(device_id: str) -> Volume:
        if device is None:
            return Volume(current_volume=0, max_volume=0)

        return device.get_volume()

    @app.put("/device/set_volume")
    def set_volume(device_id: str, volume: int):
        if device is None:
            return {"message": "No device configured"}

        device.set_volume(volume)
        return {"message": "Ok"}

    @app.get("/favorite/list/{type}")
    def list_favorite(
        type: SearchType,
        filter: str,
        offset: int = 0,
        limit: int = 10,
    ):
        # TODO: return all sources
        return (
            default_input_module()
            .list_favorite(type, filter, offset, limit)
            .model_dump(exclude_unset=True)
        )

    @app.put("/favorite/add/{type}/{id}")
    def add_favorite(type: SearchType, id: str):
        input_module_from_id(id).add_to_favorite(type, id)
        return {"message": "Ok"}

    @app.delete("/favorite/remove/{type}/{id}")
    def remove_favorite(type: SearchType, id: str):
        input_module_from_id(id).remove_from_favorite(type, id)
        return {"message": "Ok"}

    @app.get("/favorite/ids")
    def get_favorite_ids() -> FavoriteIds:
        # TODO: return all sources
        return default_input_module().get_favorite_ids()

    @app.get("/genre/list")
    def list_genre(offset: int = 0, limit: int = 25) -> GenreList:
        # TODO: return all sources
        return default_input_module().list_genre(offset=offset, limit=limit)

    @app.get("/get/{entity_id}")
    def entity_get(entity_id: str):
        try:
            entity_id_obj = EntityId.from_string(entity_id)

            return (
                input_module_from_id(entity_id)
                .get(entity_id_obj)
                .model_dump(exclude_unset=True)
            )

        except (ValueError, Exception) as e:
            logger.error(f"Failed to parse entity ID '{entity_id}': {str(e)}")
            raise HTTPException(
                status_code=400, detail=f"Invalid entity ID format: {str(e)}"
            )

    @app.post("/playlist/create")
    def playlist_create(name: str, description: str):
        # TODO support source detection / source parameter
        return (
            default_input_module()
            .playlist_create(name, description)
            .model_dump(exclude_unset=True)
        )

    @app.put("/playlist/update")
    def playlist_update(
        playlist_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ):
        return (
            input_module_from_id(playlist_id)
            .playlist_update(playlist_id, name, description)
            .model_dump(exclude_unset=True)
        )

    @app.delete("/playlist/delete")
    def playlist_delete(playlist_id: str):
        input_module_from_id(playlist_id).playlist_delete(playlist_id)
        return {"message": "Ok"}

    @app.post("/playlist/add_tracks")
    def playlist_add_tracks(
        playlist_id: str, track_ids: List[str], allow_duplicates: bool = True
    ):
        return (
            input_module_from_id(playlist_id)
            .playlist_add_tracks(playlist_id, track_ids, allow_duplicates)
            .model_dump(exclude_unset=True)
        )

    @app.get("/playlist/list")
    def playlist_user_list(offset: int = 0, limit: int = 25):
        # TODO: return all sources, support source filter
        return (
            default_input_module()
            .playlist_user_list(offset, limit)
            .model_dump(exclude_unset=True)
        )

    @app.delete("/playlist/remove_tracks")
    def playlist_remove_tracks(playlist_id: str, playlist_track_ids: List[str]):
        return (
            input_module_from_id(playlist_id)
            .playlist_remove_tracks(playlist_id, playlist_track_ids)
            .model_dump(exclude_unset=True)
        )

    @app.get("/server/config")
    def get_config():
        return config_to_wire(
            base_config=config,
            input_modules={
                name: m.config for name, m in modules.prepared_input_modules.items()
            },
            devices={name: d.config for name, d in modules.prepared_devices.items()},
        )

    @app.put("/server/restart")
    def restart_server():
        app.state.server.should_exit = True
        return {"message": "Ok"}

    @app.get("/server/modules")
    def list_modules():
        return {
            "input_modules": [
                {
                    "name": module.config.name,
                    "title": module.config.__class__.model_fields["name"].title
                    or module.config.name,
                    "enabled": module.config.enabled,
                    "state": module.health_state,
                    "error_message": module.error_message,
                }
                for module in modules.prepared_input_modules.values()
            ],
            "devices": [
                {
                    "name": device.config.name,
                    "title": device.config.__class__.model_fields["name"].title
                    or device.config.name,
                    "enabled": device.config.enabled,
                    "state": device.health_state,
                    "error_message": device.error_message,
                }
                for device in modules.prepared_devices.values()
            ],
        }

    @app.put("/server/config")
    def set_config_fields(fields: Dict[str, Any]):
        for key, value in fields.items():
            config = None
            logger.info(f"Setting config field {key} to {value}")
            attrs = key.split(".")
            if not attrs or attrs[0] != "root":
                raise HTTPException(status_code=400, detail="Invalid config key")

            attrs = attrs[1:]  # Skip the 'root' part

            if attrs[0] == "input_modules":
                module_name = attrs[1]
                if module_name in modules.prepared_input_modules:
                    config = modules.prepared_input_modules[module_name].config
                    attrs = attrs[2:]
                    if attrs[0] == "name":
                        raise HTTPException(
                            status_code=400, detail="Cannot modify 'name' field"
                        )
            elif attrs[0] == "devices":
                device_name = attrs[1]
                if device_name in modules.prepared_devices:
                    config = modules.prepared_devices[device_name].config
                    attrs = attrs[2:]
                    if attrs[0] == "name":
                        raise HTTPException(
                            status_code=400, detail="Cannot modify 'name' field"
                        )

            elif attrs[0] == "base_config":
                config = app.state.config
                attrs = attrs[1:]

            for attr in attrs[:-1]:
                if hasattr(config, attr):
                    config = getattr(config, attr)
                else:
                    raise HTTPException(
                        status_code=400, detail=f"Invalid config field: {key}"
                    )
            setattr(config, attrs[-1], value)

        return {"message": "Ok"}

    @app.get("/resource/{file_name:path}")
    async def get_resource(file_name: str):
        file_path = None
        for module in modules.prepared_input_modules.keys():
            file_path = input_module(module).get_resource_path(file_name)
            if file_path:
                resolved_path = Path(file_path).resolve()
                if resolved_path.is_file():
                    mime_type, _ = mimetypes.guess_type(str(file_path))
                    return FileResponse(resolved_path, media_type=mime_type)

        if not file_path:
            raise HTTPException(status_code=404, detail="File not found")

        resolved_path = Path(file_path).resolve()
        if not resolved_path or not resolved_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        mime_type, _ = mimetypes.guess_type(str(file_path))

        return FileResponse(resolved_path, media_type=mime_type)

    return app
