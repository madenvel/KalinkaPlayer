import logging
import logging.handlers
import multiprocessing
import os
import signal
import socket
from addons.input_module.localfiles.config_model import LocalFilesConfig
from addons.input_module.localfiles.enricher import enricher
from addons.input_module.localfiles.indexer import indexer
from addons.input_module.localfiles.input_module_db import LocalFilesInputModuleDb
from src.async_common import EventEmitter, EventListener
from addons.input_module.localfiles.localfiles import LocalFilesInputModule
from src.playqueue import PlayQueue

logger = logging.getLogger(__name__.split(".")[-1])

Config = LocalFilesConfig

_enricher_proc = None
_indexer_proc = None
_enricher_queue = multiprocessing.Queue()
_logging_queue = multiprocessing.Queue()
_log_listener = None


def is_process_running(socket_path):
    """Check if a process is running by attempting to connect to its socket."""
    if not os.path.exists(socket_path):
        return False

    try:
        # Try to connect to the socket
        test_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        test_socket.settimeout(1.0)  # Set a timeout to avoid blocking indefinitely
        test_socket.connect(socket_path)
        test_socket.close()
        return True
    except (ConnectionRefusedError, FileNotFoundError, socket.timeout):
        # If the socket file exists but connection fails, it's a stale socket
        if os.path.exists(socket_path):
            try:
                os.remove(socket_path)
            except OSError:
                pass
        return False


def setup(
    config: LocalFilesConfig,
    playqueue: PlayQueue,
    event_emitter: EventEmitter,
    event_listener: EventListener,
):
    global _enricher_proc, _indexer_proc, _enricher_queue, _logging_queue

    logger.info("Setting up localfiles input module")
    # Create specialized databases for each component
    input_module_db = LocalFilesInputModuleDb(config)

    # The LocalFilesInputModule will use its own specialized DB
    inputmodule = LocalFilesInputModule(config, input_module_db, event_emitter)

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
    listener = logging.handlers.QueueListener(_logging_queue, handler)
    listener.start()

    _indexer_proc = multiprocessing.Process(
        target=indexer.main,
        args=(config, _enricher_queue, _logging_queue),
    )

    _indexer_proc.start()

    if config.enricher.enabled:
        _enricher_proc = multiprocessing.Process(
            target=enricher.main,
            args=(config, _enricher_queue, _logging_queue),
        )
        _enricher_proc.start()

    return inputmodule


def shutdown_process(proc):
    """Shutdown a process by sending a shutdown command over its socket."""

    if proc is None or not proc.is_alive():
        logger.warning("Process is not running or already shut down.")
        return

    # Send shutdown command over the process's socket
    try:
        proc.send_signal(signal.SIGTERM)
        sleeping_time = 5
        proc.join(timeout=sleeping_time)
        if proc.is_alive():
            logger.warning(
                f"Process {proc.pid} did not shut down gracefully, killing it."
            )
            proc.kill()
        else:
            logger.info(f"Process {proc.pid} shut down successfully.")
    except Exception as e:
        logger.error(f"Error shutting down process {proc.pid}: {e}")


def shutdown():
    global _enricher_proc, _indexer_proc, _log_listener

    shutdown_process(_indexer_proc)
    shutdown_process(_enricher_proc)

    if _log_listener is not None:
        _log_listener.stop()
