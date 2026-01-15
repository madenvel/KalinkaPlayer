import json
import logging
import os

from kalinka_eventbus.bus import EventBus
from kalinka_plugin_sdk.events import (
    PlayQueueEvent,
    PlayQueueEventType,
    PlayQueueState,
)
from kalinka_plugin_sdk.inputmodule import InputModule, TrackInfo
from kalinka_plugin_sdk.api import PlayQueueController

logger = logging.getLogger(__name__.split(".")[-1])

STATE_FILE = "kalinka_state.json"


def set_state_file(file_path: str):
    global STATE_FILE
    STATE_FILE = file_path


async def save_state(
    playqueue_eventbus: EventBus[PlayQueueState, PlayQueueEventType, PlayQueueEvent],
):
    # Ensure the directory exists
    state_dir = os.path.dirname(STATE_FILE)
    if state_dir:  # Only create directory if path is not empty
        os.makedirs(state_dir, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        snapshot = playqueue_eventbus.get_snapshot()
        json.dump(
            snapshot.model_dump(),
            f,
        )
    logger.info("State saved")


async def restore_state(
    playqueue: PlayQueueController, modules: dict[str, InputModule]
):
    try:
        with open(STATE_FILE, "r") as f:
            state = PlayQueueState.model_validate_json(f.read())

        async def track_info_retriever(entity_id) -> TrackInfo:
            """Retrieve TrackInfo from EntityId using available input modules."""
            module = modules.get(entity_id.source)
            if module is None:
                raise ValueError(f"Module {entity_id.source} not found")

            track_infos = await module.get_track_info([entity_id.id])
            if not track_infos:
                raise ValueError(
                    f"Track {entity_id.id} not found in module {entity_id.source}"
                )

            return track_infos[0]

        await playqueue.restore_from_state(state, track_info_retriever)
        logger.info("State restored")
    except FileNotFoundError:
        logger.info("No state file found")
        return {}
    except json.JSONDecodeError:
        logger.error("Failed to decode state file")
        return {}
    except Exception as e:
        logger.error(f"Failed to restore state: {e}")
        return {}
