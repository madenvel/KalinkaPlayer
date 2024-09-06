import json
import logging

from src.inputmodule import InputModule
from src.playqueue import PlayQueue

logger = logging.getLogger(__name__.split(".")[-1])

STATE_FILE = "kalinka_state.json"


def set_state_file(file_path: str):
    global STATE_FILE
    STATE_FILE = file_path


def save_state(playqueue: PlayQueue, inputmodule: InputModule):
    with open(STATE_FILE, "w") as f:
        json.dump(
            {
                "current_track_id": playqueue.current_track_id,
                "track_list": [track.id for track in playqueue.track_list],
                "inputmodule": inputmodule.module_name(),
            },
            f,
        )
    logger.info("State saved")


def restore_state(playqueue: PlayQueue, inputmodule: InputModule):
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)

        if (
            "inputmodule" not in state
            or "current_track_id" not in state
            or "track_list" not in state
        ):
            return

        playqueue.current_track_id = state["current_track_id"]
        playqueue.add(inputmodule.get_track_info(state["track_list"]))
        logger.info("State restored")
    except FileNotFoundError:
        logger.info("No state file found")
        return {}
    except json.JSONDecodeError:
        logger.error("Failed to decode state file")
        return {}
