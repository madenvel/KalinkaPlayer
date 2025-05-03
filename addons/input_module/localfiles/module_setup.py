import logging
from src.async_common import EventEmitter, EventListener
from addons.input_module.localfiles import LocalFilesInputModule, get_client
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
    client = get_client(config)
    inputmodule = LocalFilesInputModule(config, client, event_emitter)

    # Start the indexer and enricher processes
    from addons.input_module.localfiles.indexer import start_indexer
    from addons.input_module.localfiles.enricher import start_enricher

    start_indexer(config, client)
    if config["enricher.enabled"]:
        start_enricher(config, client)

    return inputmodule
