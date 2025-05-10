import logging
from addons.input_module.localfiles.input_module_db import LocalFilesInputModuleDb
from addons.input_module.localfiles.indexer import (
    IndexerDb,
    stop_file_watcher,
    stop_indexer,
)
from addons.input_module.localfiles.enricher import EnricherDb, stop_enricher
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
    indexer_db = IndexerDb(config)
    enricher_db = EnricherDb(config)

    # The LocalFilesInputModule will use its own specialized DB
    inputmodule = LocalFilesInputModule(config, input_module_db, event_emitter)

    # Start the indexer and enricher processes
    from addons.input_module.localfiles.indexer import start_indexer, start_file_watcher
    from addons.input_module.localfiles.enricher import start_enricher

    start_indexer(config, indexer_db)

    # Start file watcher if enabled in configuration
    if config.get("file_watch_enabled", True):
        logger.info("Starting real-time file system monitoring")
        start_file_watcher(config)
    else:
        logger.info("Real-time file system monitoring is disabled")

    if config.get("enricher.enabled", True):
        start_enricher(config, enricher_db)

    return inputmodule


def shutdown():
    stop_file_watcher()
    stop_enricher()
    stop_indexer()
