import logging

from src.async_common import EventEmitter, EventListener
from queue import Queue

from src.ext_device import ExternalOutputDevice
from src.module_import import import_module_by_path
from src.playqueue import PlayQueue
from src.inputmodule import InputModule

from src.config import Config

logger = logging.getLogger(__name__.split(".")[-1])

prepared_input_modules = []
prepared_devices = []

queue = None
event_emitter = None
event_listener = None
playqueue = None


def setup_input_module(config, playqueue, event_emitter, event_listener) -> InputModule:
    input_modules = config["addons.input_module"]

    if not input_modules:
        return None

    current_module = None

    for key in input_modules.keys():
        if config["addons.input_module." + key + "#enabled"].lower() in [
            "yes",
            "true",
            "1",
        ]:
            logger.info(f"Enabling input module: {key}")
            current_module = key
            break

    # current_module = list(input_modules.items())[0]
    logger.info(f"Setting up input module: {current_module}")
    input = import_module_by_path(
        "addons.input_module." + current_module.lower() + ".module_setup"
    )
    inputmodule = input.setup(
        config.slice("addons.input_module." + current_module),
        playqueue,
        event_emitter,
        event_listener,
    )
    global prepared_input_modules
    prepared_input_modules.append(input)

    return inputmodule


def setup_device(
    config, playqueue, event_emitter, event_listener
) -> ExternalOutputDevice:
    devices = config["addons.device"]

    if not devices:
        return None

    current_device = list(devices.items())[0]
    logger.info(f"Enabling device control: {current_device[0]}")
    input = import_module_by_path(
        "addons.device." + current_device[0].lower() + ".module_setup"
    )

    device = input.setup(
        config.slice("addons.device." + current_device[0]),
        playqueue,
        event_emitter,
        event_listener,
    )

    global prepared_devices
    prepared_devices.append(device)
    return device


def setup(config: Config):
    global queue, event_emitter, event_listener, playqueue

    if not queue:
        queue = Queue()

    if not event_emitter:
        event_emitter = EventEmitter(queue)

    if not event_listener:
        event_listener = EventListener(queue)
    if not playqueue:
        playqueue = PlayQueue(config, event_emitter)

    inputmodule = setup_input_module(config, playqueue, event_emitter, event_listener)
    device = setup_device(config, playqueue, event_emitter, event_listener)

    return playqueue, event_listener, inputmodule, device


def shutdown():
    global prepared_input_modules, prepared_devices
    for inputmodule in prepared_input_modules:
        inputmodule.shutdown()

    for device in prepared_devices:
        device.shutdown()
