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
    EmptyList,
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

from .alsa_fallback_device import AlsaFallbackDevice
from .alsa_options import ALSA_DEVICE_PATH, make_alsa_resolver
from .config_model import KalinkaConfig
from .config_overrides import save_overrides
from .config_schema_processor import (
    build_enum_options,
    build_presentation,
    build_values,
    get_field_value,
    set_field_value,
)
from .merge_utils import get_favorite_ids_merged, k_way_merge_browse_items
from .dynamic_field_registry import build_dynamic_field_registry
from .options_registry import OptionsRegistry
from .multisearch import calculate_fuzzy_score
from .optional_packages_registry import (
    build_catalog as build_optional_packages_catalog,
    write_pending_installs,
)
from .player_setup import modules, setup, shutdown, ModuleHealthState
from .internal_modules import internal_modules
from .service_discovery import ServiceDiscovery
from .version import get_rest_api_version, get_version
from . import state_keeper
from .state_keeper import save_state, restore_state
from .test_tone import VALID_CHANNELS, play_test_tone
from .queue_ws_handler import (
    handle_websocket_connection as handle_queue_websocket_connection,
)
from .device_ws_handler import (
    handle_websocket_connection as handle_device_websocket_connection,
)


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

        # Built-in ALSA volume fallback (only when no plugin device is active).
        if getattr(app.state, "fallback_device", None) is not None:
            await app.state.fallback_device.start()

        yield

    except Exception as e:
        logger.error(f"Error: {e}")

    finally:
        logger.info("Shutting down...")
        if sd is not None:
            await sd.unregister_service()

        # Shutdown internal modules first
        await internal_modules.shutdown()

        # Stop the ALSA fallback before the playqueue: its volume monitor holds
        # a native monitor backed by the AudioPlayer the playqueue owns.
        if getattr(app.state, "fallback_device", None) is not None:
            await app.state.fallback_device.shutdown()

        # Then shutdown plugins
        await shutdown()

        app.state.player_context.playqueue_eventbus.close()
        await save_state(app.state.player_context.playqueue_eventbus)
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


async def create_app(
    overrides_file: str,
    config: KalinkaConfig,
    overrides: Dict[str, Any],
):
    app = FastAPI(lifespan=lifespan)
    app.state.config = config
    app.state.overrides_file = overrides_file
    app.state.overrides = dict(overrides)
    player_context = await setup(
        config, app.state.overrides, app.state.overrides_file
    )
    logger.info("Input modules found: %s", list(modules.prepared_input_modules.keys()))
    app.state.player_context = player_context

    # Persist the overrides dict if plugin setup reconciled it — i.e. a
    # plugin mutated config fields that came from the overrides file, so
    # ``app.state.overrides`` now diverges from disk. (One-shot triggers
    # are handled separately, persist-first *before* setup, so they're not
    # what this covers.)
    if modules.overrides_dirty:
        try:
            save_overrides(app.state.overrides_file, app.state.overrides)
            logger.info(
                "Persisted reconciled overrides to %s", app.state.overrides_file,
            )
        except OSError as exc:
            logger.error(
                "Failed to persist reconciled overrides to %s: %s",
                app.state.overrides_file,
                exc,
            )

    # Plugin classes are fixed for the process lifetime, so the dynamic-
    # field registry and the schema_version are stable. Compute once
    # here and reuse on every request; the alternative (rebuilding on
    # every GET /server/config and twice per PUT) wastes measurable
    # work for a quantity that never changes.
    app.state.dynamic_field_registry = build_dynamic_field_registry(
        modules.prepared_input_modules, modules.prepared_devices,
    )
    # Options registry — resolvers for writable enum fields whose
    # choice list depends on live system state (ALSA devices today,
    # network interfaces / COM ports tomorrow). Sits alongside the
    # dynamic-field registry but for *choices* rather than *values*.
    app.state.options_registry = OptionsRegistry()
    app.state.options_registry.register(
        ALSA_DEVICE_PATH,
        make_alsa_resolver(lambda: config.output.alsa.device),
    )
    _initial_ok_in = {
        name: m.plugin_context.config
        for name, m in modules.prepared_input_modules.items()
        if m.health_state != ModuleHealthState.ERROR
    }
    _initial_err_in = {
        name: (m.plugin_context.config, m.error_message or "Unknown error")
        for name, m in modules.prepared_input_modules.items()
        if m.health_state == ModuleHealthState.ERROR
    }
    _initial_ok_dev = {
        name: d.plugin_context.config
        for name, d in modules.prepared_devices.items()
        if d.health_state != ModuleHealthState.ERROR
    }
    _initial_err_dev = {
        name: (d.plugin_context.config, d.error_message or "Unknown error")
        for name, d in modules.prepared_devices.items()
        if d.health_state == ModuleHealthState.ERROR
    }
    app.state.schema_version = build_presentation(
        base_config=config,
        input_modules=_initial_ok_in,
        devices=_initial_ok_dev,
        input_modules_with_errors=_initial_err_in,
        devices_with_errors=_initial_err_dev,
        dynamic_field_registry=app.state.dynamic_field_registry,
    ).schema_version
    app.state.dynamic_paths = frozenset(app.state.dynamic_field_registry.keys())
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

    # No external output-device plugin selected: fall back to the built-in ALSA
    # volume control so the local output still has a working slider. It redirects
    # get/set to the native AudioPlayer (hardware mixer or software gain per
    # output.alsa.volume_mode). Started/stopped in the lifespan handler below.
    app.state.fallback_device = None
    if device is None:
        state_dir = os.path.dirname(state_keeper.STATE_FILE)
        sw_volume_path = os.path.join(
            state_dir or ".", "kalinka_device_volume.json"
        )
        app.state.fallback_device = AlsaFallbackDevice(
            player_context.playqueue,
            player_context.ext_device_eventbus,
            state_path=sw_volume_path,
        )
        device = app.state.fallback_device

    @app.get("/queue/list")
    async def read_queue_list(offset: int = 0, limit: int = 10):
        return await player_context.playqueue.list(offset=offset, limit=limit)

    @app.post("/queue/add")
    async def add_entity_to_queue(ids: list[str], index: Optional[int] = None):
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

        await player_context.playqueue.add(items, index)
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
                # Match the display title used by /server/modules — reads the
                # Pydantic `title` declared on the config's `name` field, so the
                # browse root agrees with source badges shown elsewhere.
                config = module.plugin_context.config
                display_title = (
                    config.__class__.model_fields["name"].title or config.name
                )
                result.items.append(
                    BrowseItem(
                        id=entity_id,
                        name=display_title,
                        url=f"/browse/{entity_id.to_string}",
                        can_browse=True,
                        can_add=False,
                        catalog=Catalog(
                            id=entity_id,
                            title=display_title,
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

    @app.get("/ai_search")
    async def ai_search(
        query: str,
        offset: int = 0,
        limit: int = 10,
        sources: Optional[str] = None,
    ) -> BrowseItemList:
        """Semantic / natural-language search across input modules."""
        input_modules: List[InputModule] = extract_modules(sources)
        for module in input_modules:
            result = await module.ai_search(query, offset=offset, limit=limit)
            if result.total > 0:
                return result
        return EmptyList(offset, limit)

    @app.get("/indexer/status")
    async def indexer_status(sources: Optional[str] = None) -> dict:
        """Return embedding job coverage for each configured input module."""
        input_modules: List[InputModule] = extract_modules(sources)
        result = {}
        for module in input_modules:
            if hasattr(module, "get_indexer_status"):
                try:
                    status = await module.get_indexer_status()
                    result[module.module_name()] = status
                except Exception:
                    pass
        return result

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

    @app.put("/queue/move")
    async def queue_move(from_index: int, to_index: int):
        await player_context.playqueue.move(from_index, to_index)
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

    def _partition_modules_and_devices():
        successful_input_modules = {
            name: m.plugin_context.config
            for name, m in modules.prepared_input_modules.items()
            if m.health_state != ModuleHealthState.ERROR
        }
        failed_input_modules = {
            name: (m.plugin_context.config, m.error_message or "Unknown error")
            for name, m in modules.prepared_input_modules.items()
            if m.health_state == ModuleHealthState.ERROR
        }
        successful_devices = {
            name: d.plugin_context.config
            for name, d in modules.prepared_devices.items()
            if d.health_state != ModuleHealthState.ERROR
        }
        failed_devices = {
            name: (d.plugin_context.config, d.error_message or "Unknown error")
            for name, d in modules.prepared_devices.items()
            if d.health_state == ModuleHealthState.ERROR
        }
        return (
            successful_input_modules,
            failed_input_modules,
            successful_devices,
            failed_devices,
        )

    @app.get("/server/config/schema")
    def get_config_schema():
        ok_in, err_in, ok_dev, err_dev = _partition_modules_and_devices()
        schema = build_presentation(
            base_config=config,
            input_modules=ok_in,
            devices=ok_dev,
            input_modules_with_errors=err_in,
            devices_with_errors=err_dev,
            dynamic_field_registry=app.state.dynamic_field_registry,
        )
        return schema.model_dump(mode="json")

    @app.get("/server/config")
    async def get_config():
        ok_in, _err_in, ok_dev, _err_dev = _partition_modules_and_devices()
        values = await build_values(
            base_config=config,
            input_modules=ok_in,
            devices=ok_dev,
            dynamic_entries=app.state.dynamic_field_registry.values(),
        )
        # Resolve dynamic-options enums (ALSA devices etc.) per-request
        # so hot-plug is reflected without a schema bump. Each entry is
        # a list of {value, label} option specs.
        enum_options = await build_enum_options(app.state.options_registry)
        return {
            "schema_version": app.state.schema_version,
            "values": values,
            "enum_options": {
                path: [opt.model_dump(mode="json") for opt in opts]
                for path, opts in enum_options.items()
            },
        }

    @app.get("/server/version")
    def get_version_info():
        """Get version information for the server."""
        return {
            "server_version": get_version(),
            "api_version": get_rest_api_version(),
            "name": "kalinka-player",
        }

    @app.put("/server/restart")
    async def restart_server(payload: Optional[Dict[str, Any]] = None):
        """Request a full systemd-driven restart of the server.

        Before triggering the restart, the server polls every loaded
        plugin's ``required_packages()`` and unions the result with an
        optional ``{"install": [...]}`` body. Any allowed keys land in
        ``/var/lib/kalinka/pending_installs.json`` so the bootstrap-time
        install runs automatically on the way back up. This keeps the
        optional-packages mechanism invisible from the user's perspective:
        flipping a sub-feature toggle and clicking restart just works,
        with a longer restart while the install completes.

        The explicit body is kept as a power-user override (and for
        future "retry failed install" affordances). Unknown keys in
        the body are rejected with 400; keys returned by plugins'
        ``required_packages()`` but absent from the registry are
        dropped with a log line (the plugin is misconfigured, but the
        restart itself should still proceed).
        """
        # Explicit body keys
        body_requested: list[str] = []
        if payload and isinstance(payload, dict):
            raw = payload.get("install") or []
            if raw and not isinstance(raw, list):
                raise HTTPException(
                    status_code=400, detail="'install' must be a list of keys"
                )
            body_requested = [str(k) for k in raw]

        # Auto-collect from plugins' required_packages(). Defensive: a
        # misbehaving plugin must not block the restart.
        auto_keys: list[str] = []
        for prepared in list(modules.prepared_input_modules.values()) + list(
            modules.prepared_devices.values()
        ):
            instance = prepared.plugin_instance
            if instance is None:
                continue
            try:
                keys = await instance.required_packages()
            except Exception as e:  # noqa: BLE001 — defensive
                logger.warning(
                    "Plugin %s required_packages() raised; ignoring: %s",
                    prepared.plugin_class.PLUGIN_ID,
                    e,
                )
                continue
            for k in keys or []:
                if k not in auto_keys:
                    auto_keys.append(str(k))

        # Union, body first (so explicit user intent ordering wins for
        # display) then auto-detected.
        combined: list[str] = []
        seen: set[str] = set()
        for k in list(body_requested) + list(auto_keys):
            if k not in seen:
                seen.add(k)
                combined.append(k)

        accepted: list[str] = []
        rejected: list[str] = []
        if combined:
            try:
                accepted, rejected = write_pending_installs(
                    combined,
                    modules.prepared_input_modules,
                    modules.prepared_devices,
                )
            except OSError as e:
                logger.error("Failed to write pending installs: %s", e)
                raise HTTPException(
                    status_code=500,
                    detail="Failed to record install request",
                ) from e
            # Body-provided keys must be valid (user-facing API). Keys
            # we auto-collected from plugins are best-effort — drop with
            # a log if the plugin is misconfigured.
            body_rejected = [k for k in rejected if k in body_requested]
            auto_rejected = [k for k in rejected if k not in body_requested]
            if auto_rejected:
                logger.warning(
                    "Plugin required_packages() returned keys not in the "
                    "registry; dropping: %s",
                    auto_rejected,
                )
            if body_rejected:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": "Unknown optional-package keys",
                        "rejected": body_rejected,
                        "accepted": accepted,
                    },
                )
            if accepted:
                logger.info(
                    "Queued optional packages for next-boot install: %s",
                    accepted,
                )

        # Touch the trigger file watched by kalinka-restart.path. systemd's
        # path unit fires the (root-owned) kalinka-restart.service oneshot,
        # which runs `systemctl restart kalinka.service`. We exit only when
        # systemd sends SIGTERM, letting the lifespan teardown run normally.
        trigger = Path("/run/kalinka/restart-request")
        try:
            trigger.parent.mkdir(parents=True, exist_ok=True)
            trigger.touch()
        except OSError as e:
            logger.error("Failed to write restart trigger %s: %s", trigger, e)
            raise HTTPException(
                status_code=500, detail="Failed to request restart"
            ) from e
        return {"message": "restarting", "install_queued": accepted}

    @app.post("/server/test_tone")
    async def server_test_tone(payload: Optional[Dict[str, Any]] = None):
        """Play a short test tone through the ALSA output (speaker check).

        Body (all fields optional):
          ``{"channel": "left"|"right"|"both", "device": "<alsa id>"}``

        ``device`` overrides the configured output so the client can test a
        selection that hasn't been applied yet (the setup wizard stages the
        ALSA device until its final restart). Any current playback is
        stopped first — hw: devices are exclusive, and a speaker test in
        the middle of music would be meaningless anyway. Returns once the
        tone finished playing (~2 seconds).
        """
        payload = payload or {}
        channel = str(payload.get("channel", "both")).lower()
        if channel not in VALID_CHANNELS:
            raise HTTPException(
                status_code=400,
                detail=f"channel must be one of {list(VALID_CHANNELS)}",
            )
        device = payload.get("device")
        if device is not None and not isinstance(device, str):
            raise HTTPException(status_code=400, detail="device must be a string")

        # Release the audio device before the tone opens it.
        await player_context.playqueue.stop()
        try:
            await play_test_tone(
                player_context.playqueue.config, channel=channel, device=device
            )
        except (RuntimeError, TimeoutError) as e:
            logger.error("Test tone failed: %s", e)
            raise HTTPException(
                status_code=500, detail=f"Test tone failed: {e}"
            ) from e
        return {"message": "Ok", "channel": channel}

    @app.get("/server/optional_packages")
    def get_optional_packages():
        """Return the catalog of plugin-declared optional packages.

        Each entry includes pip_spec, description, import_name, whether
        the package resolves in the current process, and whether the key
        is in the pending-installs queue. Which packages a *running*
        plugin currently needs is surfaced via
        ``GET /server/modules`` → ``missing_packages`` on each module.
        """
        return build_optional_packages_catalog(
            modules.prepared_input_modules,
            modules.prepared_devices,
        )

    async def _module_entry(prepared) -> Dict[str, Any]:
        """Roll-up live state for one prepared plugin.

        If the plugin failed to set up (or is disabled in config), the
        setup-time health_state and error_message are authoritative.
        Otherwise call plugin_instance.get_state() so plugins with
        internal sub-features can report WARNING + a message + the
        optional-package keys they're missing.
        """
        cfg = prepared.plugin_context.config
        entry: Dict[str, Any] = {
            "name": cfg.name,
            "title": cfg.__class__.model_fields["name"].title or cfg.name,
            "enabled": cfg.enabled,
            "state": prepared.health_state.value,
            "error_message": prepared.error_message,
            "missing_packages": [],
        }
        if (
            prepared.health_state == ModuleHealthState.READY
            and prepared.plugin_instance is not None
        ):
            try:
                live = await prepared.plugin_instance.get_state()
                # Unconditional override: when health_state is READY the
                # setup-time error_message is always None, so we replace
                # it with whatever the live state says (which can also
                # legitimately be None when the plugin is happy).
                entry["state"] = live.state.value
                entry["error_message"] = live.message
                entry["missing_packages"] = list(live.missing_packages)
            except Exception as e:  # noqa: BLE001 — defensive
                logger.warning(
                    "Plugin %s get_state() raised; falling back to setup-time "
                    "state: %s",
                    cfg.name,
                    e,
                )
        return entry

    @app.get("/server/modules")
    async def list_modules():
        input_entries = [
            await _module_entry(m)
            for m in modules.prepared_input_modules.values()
        ]
        device_entries = [
            await _module_entry(d) for d in modules.prepared_devices.values()
        ]
        return {"input_modules": input_entries, "devices": device_entries}

    @app.put("/server/config")
    async def set_config_fields(payload: Dict[str, Any]):
        """Apply staged changes.

        Body: `{"schema_version": "...", "changes": {"<dotted.path>": value, ...}}`.
        Paths are relative to one of the three roots: `base_config.*`,
        `input_modules.<name>.*`, or `devices.<name>.*`. No `root.` or
        `.fields.` wrappers.
        """
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Body must be a JSON object")

        client_version = payload.get("schema_version")
        if client_version and client_version != app.state.schema_version:
            raise HTTPException(
                status_code=409,
                detail="Stale schema_version; refetch /server/config/schema and retry.",
            )

        changes = payload.get("changes", payload)
        if not isinstance(changes, dict):
            raise HTTPException(
                status_code=400, detail="'changes' must be a JSON object"
            )

        dynamic_paths = app.state.dynamic_paths
        applied: Dict[str, Any] = {}

        try:
            for key, value in changes.items():
                logger.info("Setting config field %s to %r", key, value)
                attrs = key.split(".") if isinstance(key, str) else []
                if not attrs or any(p == "" for p in attrs):
                    raise HTTPException(status_code=400, detail="Invalid config key")
                if key in dynamic_paths:
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{key}' is a dynamic (plugin-resolved) field and "
                        "cannot be written via /server/config",
                    )

                target_config = None
                if attrs[0] == "base_config":
                    target_config = app.state.config
                    attrs = attrs[1:]
                elif attrs[0] == "input_modules":
                    if len(attrs) < 3:
                        raise HTTPException(status_code=400, detail="Invalid config key")
                    module_name = attrs[1]
                    if module_name in modules.prepared_input_modules:
                        target_config = modules.prepared_input_modules[
                            module_name
                        ].plugin_context.config
                        attrs = attrs[2:]
                elif attrs[0] == "devices":
                    if len(attrs) < 3:
                        raise HTTPException(status_code=400, detail="Invalid config key")
                    device_name = attrs[1]
                    if device_name in modules.prepared_devices:
                        target_config = modules.prepared_devices[
                            device_name
                        ].plugin_context.config
                        attrs = attrs[2:]

                if target_config is None or not attrs:
                    raise HTTPException(status_code=400, detail="Invalid config key")
                if attrs[0] == "name":
                    raise HTTPException(
                        status_code=400, detail="Cannot modify 'name' field"
                    )

                try:
                    set_field_value(target_config, attrs, value)
                except (AttributeError, IndexError, TypeError, ValueError) as exc:
                    logger.warning("Invalid config key '%s': %s", key, exc)
                    raise HTTPException(
                        status_code=400, detail="Invalid config key"
                    ) from exc
                applied[key] = value
                logger.info(
                    "Set %s to %r, saved: %r",
                    ".".join(attrs),
                    value,
                    get_field_value(target_config, attrs),
                )
        finally:
            # Persist whatever stuck in memory so a restart matches the
            # live state, even if a later change in the batch was rejected.
            if applied:
                app.state.overrides.update(applied)
                try:
                    save_overrides(app.state.overrides_file, app.state.overrides)
                except OSError as exc:
                    logger.error(
                        "Failed to persist overrides to %s: %s",
                        app.state.overrides_file,
                        exc,
                    )

        return {"message": "Ok", "schema_version": app.state.schema_version}

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
