import logging
import logging.handlers
import multiprocessing
from typing import ClassVar, Optional

from kalinka_plugin_sdk import OptionalPackageSpec
from kalinka_plugin_sdk.plugin import InputPluginContext, InputModulePlugin
from kalinka_plugin_sdk.inputmodule import InputModule

from .config_model import LocalFilesConfig
from .db_schema import init_db
from .input_module_db import LocalFilesInputModuleDb
from .localfiles import LocalFilesInputModule
from .optional_packages import OPTIONAL_PACKAGES
from . import enricher
from . import indexer
from . import embedder
from . import searcher


logger = logging.getLogger(__name__.split(".")[-1])


class KalinkaPluginLocalFiles(InputModulePlugin):
    REQUIRES_SDK = ">=1.0,<2"
    PLUGIN_ID = "localfiles"
    CONFIG_MODEL = LocalFilesConfig
    OPTIONAL_PACKAGES: ClassVar[dict[str, OptionalPackageSpec]] = OPTIONAL_PACKAGES

    def __init__(self):
        self._enricher_proc = None
        self._indexer_proc = None
        self._searcher_proc = None
        self._embedder_proc = None
        self._enricher_queue = multiprocessing.Queue()
        self._searcher_nudge_queue = multiprocessing.Queue()
        self._embedder_nudge_queue = multiprocessing.Queue()
        self._logging_queue = multiprocessing.Queue()
        self._search_request_queue = multiprocessing.Queue()
        self._search_response_queue = multiprocessing.Queue()
        self._text_encode_request_queue = multiprocessing.Queue()
        self._text_encode_response_queue = multiprocessing.Queue()
        self._log_listener = None
        self._inputmodule = None

    def module_name(self) -> str:
        return "Local Files Input Module"

    def get_interface(self) -> Optional[InputModule]:
        return self._inputmodule

    async def setup(self, context: InputPluginContext) -> None:
        config = LocalFilesConfig(**context.config.model_dump())
        logger.info("Setting up localfiles input module")

        input_module_db = LocalFilesInputModuleDb(config)

        # If rescan_on_startup was set, LocalFilesInputModuleDb will have reset
        # it to False on the local copy. Propagate that back to context.config
        # so the change is persisted to disk when the server saves config on shutdown.
        if context.config.rescan_on_startup and not config.rescan_on_startup:
            context.config.rescan_on_startup = False
            logger.info("rescan_on_startup reset to False after purge")

        # The LocalFilesInputModule will use its own specialized DB
        self._inputmodule = LocalFilesInputModule(
            config,
            input_module_db,
            self._search_request_queue,
            self._search_response_queue,
        )

        # Forward subprocess log records into the main logging pipeline so the
        # main kalinka-server handlers (and their levels/formatters) decide what
        # to emit. This avoids a parallel StreamHandler that would bypass the
        # server's log level filtering.
        class SubprocessForwardingHandler(logging.Handler):
            def emit(self, record):
                target = logging.getLogger(record.name)
                if target.isEnabledFor(record.levelno):
                    target.handle(record)

        handler = SubprocessForwardingHandler()

        # In Python 3.14, respect_handler_level defaults to True; we keep it
        # False for backward compatibility and to let the target logger handle
        # level filtering.
        self._log_listener = logging.handlers.QueueListener(
            self._logging_queue, handler, respect_handler_level=False
        )
        self._log_listener.start()

        # Centralised schema init — runs once in the main process before
        # any subprocess starts, so there is no lock contention.
        await init_db(config.db_path)

        self._indexer_proc = multiprocessing.Process(
            target=indexer.main,
            args=(config, self._enricher_queue, self._logging_queue),
        )

        self._indexer_proc.start()

        if config.enricher.enabled:
            # Enricher nudges the searcher (not the embedder directly)
            self._enricher_proc = multiprocessing.Process(
                target=enricher.main,
                args=(
                    config,
                    self._enricher_queue,
                    self._logging_queue,
                    self._searcher_nudge_queue,
                ),
            )
            self._enricher_proc.start()

        # The embedder process must run when:
        # 1. embedder.enabled — to compute CLAP audio/text embeddings, or
        # 2. searcher needs KNN — to serve text-encode requests for search.
        clap_version = config.embedder.clap.current_version
        need_embedder = config.embedder.enabled or (
            config.searcher.enabled and clap_version > 0
        )

        if config.searcher.enabled:
            # Searcher owns tags + FTS + search; nudges embedder when tags are done.
            # Text-encode queues let the searcher request CLAP encoding from
            # the embedder process instead of loading the ~600 MB model itself.
            text_encode_queues = (
                (self._text_encode_request_queue, self._text_encode_response_queue)
                if need_embedder
                else (None, None)
            )
            self._searcher_proc = multiprocessing.Process(
                target=searcher.main,
                args=(
                    config,
                    self._logging_queue,
                    self._search_request_queue,
                    self._search_response_queue,
                    self._searcher_nudge_queue,
                    self._embedder_nudge_queue,
                    *text_encode_queues,
                ),
            )
            self._searcher_proc.start()

        if need_embedder:
            # Embedder is CLAP-only; also serves text-encode requests for
            # the searcher so only one process loads the CLAP model.
            self._embedder_proc = multiprocessing.Process(
                target=embedder.main,
                args=(
                    config,
                    self._logging_queue,
                    self._embedder_nudge_queue,
                    self._text_encode_request_queue,
                    self._text_encode_response_queue,
                ),
            )
            self._embedder_proc.start()

    def _shutdown_process(self, proc):
        """Shutdown a process by sending a termination signal"""

        if proc is None or not proc.is_alive():
            logger.warning(f"Process is not running or already shut down.")
            return

        # Send shutdown command over the process's socket
        try:
            sleeping_time = 5
            proc.terminate()
            proc.join(timeout=sleeping_time)
            if proc.is_alive():
                logger.warning(
                    f"Process {proc.pid} did not shut down gracefully, killing it."
                )
                proc.kill()
                proc.join(timeout=sleeping_time)
            else:
                logger.info(f"Process {proc.pid} shut down successfully.")
        except Exception as e:
            logger.error(f"Error shutting down process {proc.pid}: {e}")

    async def shutdown(self) -> None:
        logger.info("Shutting down localfiles input module")

        self._shutdown_process(self._indexer_proc)
        self._shutdown_process(self._enricher_proc)
        self._shutdown_process(self._searcher_proc)
        self._shutdown_process(self._embedder_proc)

        if self._log_listener is not None:
            self._log_listener.stop()

        # Ensure multiprocessing queues release their semaphores
        for q in (
            self._enricher_queue,
            self._searcher_nudge_queue,
            self._embedder_nudge_queue,
            self._logging_queue,
            self._search_request_queue,
            self._search_response_queue,
            self._text_encode_request_queue,
            self._text_encode_response_queue,
        ):
            if q is not None:
                q.close()
                q.join_thread()
