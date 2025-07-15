import json
import logging

from data_model.datamodel import EntityId
from src.inputmodule import InputModule, TrackInfo
from src.playqueue import PlayQueue

logger = logging.getLogger(__name__.split(".")[-1])

STATE_FILE = "kalinka_state.json"


def set_state_file(file_path: str):
    global STATE_FILE
    STATE_FILE = file_path


def save_state(playqueue: PlayQueue):
    with open(STATE_FILE, "w") as f:
        json.dump(
            {
                "current_track_id": playqueue.current_track_id,
                "track_list": [track.id.model_dump() for track in playqueue.track_list],
            },
            f,
        )
    logger.info("State saved")


def add_items_to_playqueue(
    playqueue: PlayQueue, ids: list[str], modules: dict[str, InputModule]
):
    items: list[TrackInfo] = []
    for entity_id in ids:
        entity_id_obj = EntityId.from_string(entity_id)
        module = modules.get(entity_id_obj.source, None)

        if module is None:
            logger.warning(f"Module {entity_id_obj.source} is not found")
            continue

        items.extend(module.get_track_info([entity_id_obj.id]))

    playqueue.add(items)


def restore_state(playqueue: PlayQueue, modules: dict[str, InputModule]):
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)

        if "current_track_id" not in state or "track_list" not in state:
            return

        add_items_to_playqueue(playqueue, state["track_list"], modules)
        playqueue.current_track_id = state["current_track_id"]
        logger.info("State restored")
    except FileNotFoundError:
        logger.info("No state file found")
        return {}
    except json.JSONDecodeError:
        logger.error("Failed to decode state file")
        return {}
