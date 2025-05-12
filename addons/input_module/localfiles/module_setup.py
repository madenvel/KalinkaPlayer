import logging
from addons.input_module.localfiles.input_module_db import LocalFilesInputModuleDb
from src.async_common import EventEmitter, EventListener
from addons.input_module.localfiles import LocalFilesInputModule
from src.config import Config
from src.playqueue import PlayQueue

logger = logging.getLogger(__name__.split(".")[-1])


def setup(
    config: Config,
    playqueue: PlayQueue,
    event_emitter: EventEmitter,
    event_listener: EventListener,
):
    logger.info("Setting up localfiles input module")
    # Create specialized databases for each component
    input_module_db = LocalFilesInputModuleDb(config)

    # The LocalFilesInputModule will use its own specialized DB
    inputmodule = LocalFilesInputModule(config, input_module_db, event_emitter)
    # Start file watcher if enabled in configuration

    return inputmodule


def shutdown():
    pass
