import asyncio
import json
import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
)
from fastapi.responses import FileResponse, StreamingResponse

from kalinka_plugin_sdk.datamodel import (
    BrowseItem,
    BrowseItemList,
    Catalog,
    EntityId,
    EntityType,
    FavoriteIds,
    GenreList,
    PlaybackState,
)
from kalinka_plugin_sdk.ext_device import DeviceVolume, ExternalOutputDevice
from kalinka_plugin_sdk.ext_device_events import ExtDeviceEventType
from kalinka_plugin_sdk.inputmodule import InputModule, SearchType, TrackInfo
from kalinka_plugin_sdk.events import PlayQueueEventType

from .config_model import KalinkaConfig
from .config_schema_processor import config_to_wire, get_field_value, set_field_value
from .merge_utils import get_favorite_ids_merged, k_way_merge_browse_items
from .multisearch import calculate_fuzzy_score
from .player_setup import modules, setup, shutdown
from .internal_modules import internal_modules
from .service_discovery import ServiceDiscovery
from .version import get_api_version, get_version
from .state_keeper import save_state, restore_state
from .queue_ws_handler import (
    handle_websocket_connection as handle_queue_websocket_connection,
)
from .device_ws_handler import (
    handle_websocket_connection as handle_device_websocket_connection,
)


def save_config(config_file: str, config: KalinkaConfig):
    """Save the configuration to a file."""
    config_data = config.model_dump()
    # Ensure the directory exists
    config_dir = os.path.dirname(config_file)
    if config_dir:  # Only create directory if path is not empty
        os.makedirs(config_dir, exist_ok=True)
    with open(config_file, "w") as f:
        json.dump(config_data, f, indent=2)
    logger.info(f"Configuration saved to {config_file}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    sd = None
    try:
        sd = ServiceDiscovery(app.state.config)
        await sd.register_service()

        # Initialize internal modules (device automation, etc.)
        await internal_modules.initialize(app.state.config, app.state.player_context)

        await restore_state(
            app.state.player_context.playqueue,
            {
                name: module.interface
                for name, module in modules.prepared_input_modules.items()
                if name in modules.enabled_input_modules
                and isinstance(module.interface, InputModule)
            },
        )
        await app.state.player_context.playqueue.__aenter__()
        yield

    except Exception as e:
        logger.error(f"Error: {e}")

    finally:
        logger.info("Shutting down...")
        if sd is not None:
            await sd.unregister_service()

        # Shutdown internal modules first
        await internal_modules.shutdown()

        # Then shutdown plugins
        await shutdown(os.path.dirname(app.state.config_file))

        app.state.player_context.playqueue_eventbus.close()
        await save_state(app.state.player_context.playqueue_eventbus)
        app.state.config.restart = False
        save_config(app.state.config_file, app.state.config)
        await app.state.player_context.playqueue.__aexit__(None, None, None)


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

    if source not in modules.enabled_input_modules:
        raise HTTPException(
            status_code=404, detail=f"Input module '{source}' is disabled"
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

    default_module = modules.prepared_input_modules[
        next(iter(modules.enabled_input_modules))
    ]

    if isinstance(default_module.interface, InputModule):
        return default_module.interface

    raise HTTPException(
        status_code=500,
        detail="Default input module interface is not initialized or of wrong type",
    )


def extract_modules(sources: Optional[str]) -> List[InputModule]:
    """Extract input modules based on the provided sources."""
    if not sources:
        return [
            module.interface
            for module_name, module in modules.prepared_input_modules.items()
            if module_name in modules.enabled_input_modules
            and isinstance(module.interface, InputModule)
        ]

    sources_split = sources.split(",")
    input_modules: list[InputModule] = []
    for source in sources_split:
        if source in modules.prepared_input_modules:
            interface = modules.prepared_input_modules[source].interface
            if isinstance(interface, InputModule):
                input_modules.append(interface)

    if not input_modules:
        raise HTTPException(status_code=404, detail="No matching input modules found")

    return input_modules


async def create_app(config_file, config: KalinkaConfig):
    app = FastAPI(lifespan=lifespan)
    app.state.config = config
    app.state.config_file = config_file
    player_context = await setup(os.path.dirname(config_file), config)
    logger.info("Input modules found: %s", list(modules.prepared_input_modules.keys()))
    app.state.player_context = player_context
    first_enabled_device_name = next(iter(modules.enabled_devices), None)
    prepared_device = (
        modules.prepared_devices[first_enabled_device_name]
        if first_enabled_device_name
        else None
    )
    device: Optional[ExternalOutputDevice] = (
        prepared_device.interface
        if prepared_device
        and isinstance(prepared_device.interface, ExternalOutputDevice)
        else None
    )

    @app.get("/queue/list")
    async def read_queue_list(offset: int = 0, limit: int = 10):
        return await player_context.playqueue.list(offset=offset, limit=limit)

    @app.post("/queue/add")
    async def add_entity_to_queue(ids: list[str]):
        items: list[TrackInfo] = []
        for entity_id in ids:
            entity_id_obj = EntityId.from_string(entity_id)
            module = input_module_from_id(entity_id)
            if entity_id_obj.type != EntityType.TRACK:
                browse_list = await module.browse(entity_id_obj, offset=0, limit=5000)
                track_ids = [
                    item.id.id
                    for item in browse_list.items
                    if item.id.type == EntityType.TRACK
                ]
                items.extend(await module.get_track_info(track_ids))
            else:
                items.extend(await module.get_track_info([entity_id_obj.id]))

        await player_context.playqueue.add(items)
        return {"message": "Items added to queue", "count": len(items)}

    @app.put("/queue/play")
    async def queue_play(index: Union[int, None] = None):
        await player_context.playqueue.play(index)
        return {"message": "Ok"}

    @app.put("/queue/pause")
    async def queue_pause(paused: bool = True):
        await player_context.playqueue.pause(paused)
        return {"message": "Ok"}

    @app.put("/queue/next")
    async def queue_next():
        await player_context.playqueue.next()
        return {"message": "Ok"}

    @app.put("/queue/prev")
    async def queue_prev():
        await player_context.playqueue.prev()
        return {"message": "Ok"}

    @app.put("/queue/stop")
    async def queue_stop():
        await player_context.playqueue.stop()
        return {"message": "Ok"}

    @app.put("/queue/current_track/seek")
    async def queue_seek(position_ms: int):
        value = await player_context.playqueue.seek(position_ms)
        return {"message": "Ok", "position_ms": value}

    @app.get("/browse")
    def browse_root(offset: int = 0, limit: int = 10):
        """Browse the root catalog."""

        result = BrowseItemList(
            offset=offset,
            limit=limit,
            total=len(modules.enabled_input_modules),
            items=[],
        )

        for module_name in sorted(modules.enabled_input_modules):
            module = modules.prepared_input_modules[module_name]
            if isinstance(module.interface, InputModule):
                entity_id = EntityId(
                    id="root", type=EntityType.CATALOG, source=module_name
                )
                result.items.append(
                    BrowseItem(
                        id=entity_id,
                        name=module.plugin_context.config.name.title() or module_name,
                        url=f"/browse/{entity_id.to_string}",
                        can_browse=True,
                        can_add=False,
                        catalog=Catalog(
                            id=entity_id,
                            title=module.plugin_context.config.name.title()
                            or module_name,
                            image=None,  # Placeholder for catalog image
                            can_genre_filter=False,
                            description="Kalinka Input Module",
                        ),
                    )
                )

        return result.model_dump(exclude_unset=True)

    @app.get("/browse/{id}")
    async def browse_entity(
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
            result = await input_module.browse(
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
        sources: Optional[str] = None,
    ) -> BrowseItemList:
        """Search for items across input modules."""
        input_modules: List[InputModule] = extract_modules(sources)

        return await k_way_merge_browse_items(
            [partial(module.search, search_type, query) for module in input_modules],
            compared_value=lambda item: calculate_fuzzy_score(item.name, query),
            offset=offset,
            limit=limit,
        )

    @app.get("/queue/events")
    async def stream(request: Request):
        async def process_events():
            async with player_context.playqueue_eventbus.stream(
                list(PlayQueueEventType)
            ) as stream:
                try:
                    async for event in stream:
                        if await request.is_disconnected():
                            break

                        if event is not None:
                            yield event.model_dump_json() + "\n"

                except asyncio.CancelledError:
                    logger.debug("Event stream cancelled")
                    return
                except Exception as e:
                    logger.error(f"Error processing events: {e}")
                    yield json.dumps({"error": str(e)}) + "\n"

        return StreamingResponse(process_events(), media_type="text/event-stream")

    @app.get("/device/events")
    async def device_stream(request: Request):
        async def process_device_events():
            async with player_context.ext_device_eventbus.stream(
                list(ExtDeviceEventType)
            ) as stream:
                try:
                    async for event in stream:
                        if await request.is_disconnected():
                            break

                        if event is not None:
                            yield event.model_dump_json() + "\n"

                except asyncio.CancelledError:
                    logger.debug("Device event stream cancelled")
                    return
                except Exception as e:
                    logger.error(f"Error processing device events: {e}")
                    yield json.dumps({"error": str(e)}) + "\n"

        return StreamingResponse(
            process_device_events(), media_type="text/event-stream"
        )

    @app.get("/queue/state")
    async def state() -> PlaybackState:
        return await player_context.playqueue.get_playback_state()

    @app.get("/queue/mode")
    async def mode():
        return await player_context.playqueue.get_playback_mode()

    @app.put("/queue/mode")
    async def set_mode(
        shuffle: Optional[bool] = None,
        repeat_single: Optional[bool] = None,
        repeat_all: Optional[bool] = None,
    ):
        await player_context.playqueue.set_playback_mode(
            shuffle, repeat_single, repeat_all
        )
        return {"message": "Ok"}

    @app.put("/queue/clear")
    async def clear():
        await player_context.playqueue.clear()
        return {"message": "Ok"}

    @app.post("/queue/remove")
    async def remove(index: int):
        await player_context.playqueue.remove([index])
        return {"message": "Ok"}

    @app.get("/device/list")
    async def device_supported_functions():
        if device is None:
            return {"message": "No device configured"}

        return device.supported_functions()

    @app.get("/device/get_volume")
    async def get_volume() -> DeviceVolume:
        if device is None:
            return DeviceVolume(supported=False)

        return await device.get_volume()

    @app.put("/device/set_volume")
    async def set_volume(volume: int):
        if device is None:
            return {"message": "No device configured"}

        await device.set_volume(volume)
        return {"message": "Ok"}

    @app.get("/favorite/list/{type}")
    async def list_favorite(
        type: SearchType,
        filter: str = "",
        sources: Optional[str] = None,
        offset: int = 0,
        limit: int = 10,
    ):
        """List favorites of a specific type."""
        input_modules: list[InputModule] = extract_modules(sources)

        return await k_way_merge_browse_items(
            [partial(module.list_favorite, type, filter) for module in input_modules],
            compared_value=lambda item: item.timestamp,
            offset=offset,
            limit=limit,
        )

    @app.put("/favorite/add/{id}")
    async def add_favorite(id: str):
        await input_module_from_id(id).add_to_favorite(id)
        return {"message": "Ok"}

    @app.delete("/favorite/remove/{id}")
    async def remove_favorite(id: str):
        await input_module_from_id(id).remove_from_favorite(id)
        return {"message": "Ok"}

    @app.get("/favorite/ids")
    async def get_favorite_ids(source: Optional[str] = None) -> FavoriteIds:
        input_modules: list[InputModule] = extract_modules(source)

        return await get_favorite_ids_merged(modules=input_modules)

    @app.get("/genre/list")
    async def list_genre(
        source: Optional[str] = None, offset: int = 0, limit: int = 25
    ) -> GenreList:
        genre_list = GenreList(offset=offset, limit=limit, total=0, items=[])
        for module_name in modules.enabled_input_modules:
            if (source is not None) and (source != module_name):
                continue
            module = modules.prepared_input_modules[module_name]
            if isinstance(module.interface, InputModule):
                result = await module.interface.list_genre(offset=offset, limit=limit)
                genre_list.items.extend(result.items)
                genre_list.total += result.total
        return genre_list

    @app.get("/get/{entity_id}")
    async def entity_get(entity_id: str):
        try:
            entity_id_obj = EntityId.from_string(entity_id)

            return (
                await input_module_from_id(entity_id).get(entity_id_obj)
            ).model_dump(exclude_unset=True)

        except (ValueError, Exception) as e:
            logger.error(f"Failed to parse entity ID '{entity_id}': {str(e)}")
            raise HTTPException(
                status_code=400, detail=f"Invalid entity ID format: {str(e)}"
            )

    @app.post("/playlist/create")
    async def playlist_create(name: str, description: str, source: str = "localfiles"):
        return (
            await input_module(source).playlist_create(name, description)
        ).model_dump(exclude_unset=True)

    @app.put("/playlist/update")
    async def playlist_update(
        playlist_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ):
        return (
            await input_module_from_id(playlist_id).playlist_update(
                playlist_id, name, description
            )
        ).model_dump(exclude_unset=True)

    @app.delete("/playlist/delete")
    async def playlist_delete(playlist_id: str):
        await input_module_from_id(playlist_id).playlist_delete(playlist_id)
        return {"message": "Ok"}

    @app.post("/playlist/add_tracks")
    async def playlist_add_tracks(
        playlist_id: str, track_ids: List[str], allow_duplicates: bool = True
    ):
        return (
            await input_module_from_id(playlist_id).playlist_add_tracks(
                playlist_id, track_ids, allow_duplicates
            )
        ).model_dump(exclude_unset=True)

    @app.get("/playlist/list")
    async def playlist_user_list(
        sources: Optional[str] = None, offset: int = 0, limit: int = 25
    ):
        modules: list[InputModule] = extract_modules(sources)

        return await k_way_merge_browse_items(
            [module.playlist_user_list for module in modules],
            compared_value=lambda item: item.timestamp,
            offset=offset,
            limit=limit,
        )

    @app.delete("/playlist/remove_tracks")
    async def playlist_remove_tracks(playlist_id: str, playlist_track_ids: List[str]):
        return (
            await input_module_from_id(playlist_id).playlist_remove_tracks(
                playlist_id, playlist_track_ids
            )
        ).model_dump(exclude_unset=True)

    @app.get("/server/config")
    def get_config():
        return config_to_wire(
            base_config=config,
            input_modules={
                name: m.plugin_context.config
                for name, m in modules.prepared_input_modules.items()
            },
            devices={
                name: d.plugin_context.config
                for name, d in modules.prepared_devices.items()
            },
        )

    @app.get("/server/version")
    def get_version_info():
        """Get version information for the server."""
        return {
            "server_version": get_version(),
            "api_version": get_api_version(),
            "name": "kalinka-player",
        }

    @app.put("/server/restart")
    def restart_server():
        app.state.server.should_exit = True
        return {"message": "Ok"}

    @app.get("/server/modules")
    def list_modules():
        return {
            "input_modules": [
                {
                    "name": module.plugin_context.config.name,
                    "title": module.plugin_context.config.__class__.model_fields[
                        "name"
                    ].title
                    or module.plugin_context.config.name,
                    "enabled": module.plugin_context.config.enabled,
                    "state": module.health_state,
                    "error_message": module.error_message,
                }
                for module in modules.prepared_input_modules.values()
            ],
            "devices": [
                {
                    "name": device.plugin_context.config.name,
                    "title": device.plugin_context.config.__class__.model_fields[
                        "name"
                    ].title
                    or device.plugin_context.config.name,
                    "enabled": device.plugin_context.config.enabled,
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
                    config = modules.prepared_input_modules[
                        module_name
                    ].plugin_context.config
                    attrs = attrs[2:]
                    if attrs[0] == "name":
                        raise HTTPException(
                            status_code=400, detail="Cannot modify 'name' field"
                        )
            elif attrs[0] == "devices":
                device_name = attrs[1]
                if device_name in modules.prepared_devices:
                    config = modules.prepared_devices[device_name].plugin_context.config
                    attrs = attrs[2:]
                    if attrs[0] == "name":
                        raise HTTPException(
                            status_code=400, detail="Cannot modify 'name' field"
                        )
            elif attrs[0] == "base_config":
                config = app.state.config
                attrs = attrs[1:]

            if config is None:
                raise HTTPException(status_code=400, detail="Invalid config key")

            set_field_value(config, attrs, value)
            logger.info(
                f"Set config field {'.'.join(attrs)} to {value}, saved value: {get_field_value(config, attrs)}"
            )

        return {"message": "Ok"}

    @app.get("/resource/{file_name:path}")
    async def get_resource(file_name: str):
        file_path = None
        for module_name in modules.enabled_input_modules:
            file_path = await input_module(module_name).get_resource_path(file_name)
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

    @app.websocket("/queue/ws")
    async def queue_websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time playback control and event streaming."""
        await handle_queue_websocket_connection(
            websocket, player_context.playqueue_eventbus, player_context.playqueue
        )

    @app.websocket("/device/ws")
    async def device_websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for device control and event streaming."""
        if device is None:
            await websocket.accept()
            await websocket.send_json({"error": "No device configured"})
            await websocket.close()
            return

        await handle_device_websocket_connection(
            websocket, player_context.ext_device_eventbus, device
        )

    return app
