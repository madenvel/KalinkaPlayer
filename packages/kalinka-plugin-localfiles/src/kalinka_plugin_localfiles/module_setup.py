import logging
import logging.handlers
import multiprocessing
from typing import Optional

from kalinka_plugin_sdk.api import PluginContext, InputModulePlugin
from kalinka_plugin_sdk.inputmodule import InputModule

from .config_model import LocalFilesConfig
from .input_module_db import LocalFilesInputModuleDb
from .localfiles import LocalFilesInputModule
from . import enricher
from . import indexer


logger = logging.getLogger(__name__.split(".")[-1])


class KalinkaPluginLocalFiles(InputModulePlugin):
    REQUIRES_SDK = ">=1.0,<2"
    PLUGIN_ID = "localfiles"
    CONFIG_MODEL = LocalFilesConfig

    def __init__(self):
        self._enricher_proc = None
        self._indexer_proc = None
        self._enricher_queue = multiprocessing.Queue()
        self._logging_queue = multiprocessing.Queue()
        self._log_listener = None
        self._inputmodule = None

    def module_name(self) -> str:
        return "Local Files Input Module"

    def get_interface(self) -> Optional[InputModule]:
        return self._inputmodule

    def setup(self, context: PluginContext) -> None:
        config = LocalFilesConfig(**context.config.model_dump())
        logger.info("Setting up localfiles input module")

        input_module_db = LocalFilesInputModuleDb(config)

        # The LocalFilesInputModule will use its own specialized DB
        self._inputmodule = LocalFilesInputModule(
            config, input_module_db, context.event_emitter
        )

        handler = logging.StreamHandler()
        handler.setLevel(logger.level)
        # Use the same formatter as the parent process root logger
        root_logger = logging.getLogger()
        if root_logger.handlers and root_logger.handlers[0].formatter:
            handler.setFormatter(root_logger.handlers[0].formatter)
        else:
            # Fallback to a reasonable default format if no formatter is found
            formatter = logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)s %(thread)d %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(formatter)
        self._log_listener = logging.handlers.QueueListener(
            self._logging_queue, handler
        )
        self._log_listener.start()

        self._indexer_proc = multiprocessing.Process(
            target=indexer.main,
            args=(config, self._enricher_queue, self._logging_queue),
        )

        self._indexer_proc.start()

        if config.enricher.enabled:
            self._enricher_proc = multiprocessing.Process(
                target=enricher.main,
                args=(config, self._enricher_queue, self._logging_queue),
            )
            self._enricher_proc.start()

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

    def shutdown(self) -> None:
        logger.info("Shutting down localfiles input module")

        self._shutdown_process(self._indexer_proc)
        self._shutdown_process(self._enricher_proc)

        if self._log_listener is not None:
            self._log_listener.stop()

        # Ensure multiprocessing queues release their semaphores
        for q in (self._enricher_queue, self._logging_queue):
            if q is not None:
                q.close()
                q.join_thread()
