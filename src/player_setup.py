import logging

from src.async_common import EventEmitter, EventListener
from queue import Queue

from src.ext_device import ExternalOutputDevice
from src.module_import import import_module_by_path
from src.playqueue import EventType, PlayQueue
from src.inputmodule import InputModule
from addons.device.musiccast.musiccast import Device

from src.config import config

logger = logging.getLogger(__name__.split(".")[-1])


def setup_input_module(playqueue, event_emitter, event_listener) -> InputModule:
    input_modules = config["addons"]["input_module"]

    if not input_modules:
        return None

    current_module = list(input_modules.items())[0]
    logger.info(f"Setting up input module: {current_module[0]}")
    input = import_module_by_path(
        "addons.input_module." + current_module[0].lower() + ".module_setup"
    )
    return input.setup(playqueue, event_emitter, event_listener)


def setup_device(playqueue, event_emitter, event_listener) -> ExternalOutputDevice:
    devices = config["addons"]["device"]

    if not devices:
        return None

    current_device = list(devices.items())[0]
    logger.info(f"Enabling device control: {current_device[0]}")
    input = import_module_by_path(
        "addons.device." + current_device[0].lower() + ".module_setup"
    )

    return input.setup(playqueue, event_emitter, event_listener)


def setup():
    queue = Queue()
    event_emitter = EventEmitter(queue)
    event_listener = EventListener(queue)
    playqueue = PlayQueue(event_emitter)
    inputmodule = setup_input_module(playqueue, event_emitter, event_listener)
    device = setup_device(playqueue, event_emitter, event_listener)

    return playqueue, event_listener, inputmodule, device
