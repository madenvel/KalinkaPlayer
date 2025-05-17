import json
import logging
import os
import socket
import tempfile
import subprocess
import sys
import threading
from pathlib import Path
from addons.input_module.localfiles.input_module_db import LocalFilesInputModuleDb
from src.async_common import EventEmitter, EventListener
from addons.input_module.localfiles import LocalFilesInputModule
from src.config import Config
from src.playqueue import PlayQueue

logger = logging.getLogger(__name__.split(".")[-1])

# Socket paths for IPC
INDEXER_SOCKET_PATH = os.path.join(tempfile.gettempdir(), "kalinka-indexer.sock")
ENRICHER_SOCKET_PATH = os.path.join(tempfile.gettempdir(), "kalinka-enricher.sock")


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


def spawn_process(script_path, config_json):
    """Spawn a process with the given script and config."""
    # Get the directory of the current script
    base_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    script_full_path = base_dir / script_path

    # Ensure the script exists
    if not script_full_path.exists():
        logger.error(f"Script not found: {script_full_path}")
        return None

    logger.info(f"Spawning process: {script_full_path}")

    # Create log readers for the subprocess
    def log_reader(pipe, level, prefix):
        """Reads from pipe and logs each line with the specified level and prefix."""
        process_name = os.path.basename(script_path).split(".")[0]
        for line in iter(pipe.readline, b""):
            line_str = line.decode("utf-8", errors="replace").strip()
            if line_str:
                logger.log(level, f"[{process_name}] {prefix}: {line_str}")
        pipe.close()

    try:
        # Use subprocess.Popen to spawn the process
        process = subprocess.Popen(
            [sys.executable, str(script_full_path), "-c", config_json],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # Detach the process from parent
            bufsize=1,  # Line buffered
            universal_newlines=False,  # We'll decode manually
        )

        # Start separate threads to read stdout and stderr
        import threading

        stdout_thread = threading.Thread(
            target=log_reader, args=(process.stdout, logging.INFO, "OUT"), daemon=True
        )
        stderr_thread = threading.Thread(
            target=log_reader, args=(process.stderr, logging.ERROR, "ERR"), daemon=True
        )

        stdout_thread.start()
        stderr_thread.start()

        return process
    except Exception as e:
        logger.error(f"Failed to spawn process {script_path}: {str(e)}")
        return None


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

    # # Convert config to JSON for passing to child processes
    # config_json = json.dumps(config.flatten_config())

    # # Check if indexer is running, if not start it
    # if not is_process_running(INDEXER_SOCKET_PATH):
    #     logger.info("Indexer not running, starting indexer process")
    #     spawn_process("indexer/indexer.py", config_json)
    # else:
    #     logger.info("Indexer already running")

    # # Check if enricher is running, if not start it
    # if not is_process_running(ENRICHER_SOCKET_PATH):
    #     logger.info("Enricher not running, starting enricher process")
    #     spawn_process("enricher/enricher.py", config_json)
    # else:
    #     logger.info("Enricher already running")

    return inputmodule


def shutdown():
    # We don't need to stop the indexer and enricher here
    # They're independent processes and will be terminated by the OS
    pass
