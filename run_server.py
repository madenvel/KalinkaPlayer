#!/usr/bin/env python3
import logging
import uvicorn

from src import config, state_keeper
from src.netutils import get_ip_address

import argparse


uvicorn_log_config = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s.%(msecs)03d %(levelname)s %(thread)d %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }
    },
    "handlers": {
        "default": {
            "level": "INFO",
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        }
    },
    "loggers": {
        "uvicorn": {
            "handlers": ["default"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.error": {
            "level": "INFO",
            "handlers": ["default"],
            "propagate": False,
        },
        "uvicorn.access": {
            "level": "INFO",
            "handlers": ["default"],
            "propagate": False,
        },
    },
}

logger = logging.getLogger(__name__.split(".")[-1])


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        action="store",
        help="Config file location",
    )
    parser.add_argument(
        "--state",
        action="store",
        help="State file location",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Set log level to debug",
        default=False,
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug is True else logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(thread)d %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Reduce logging level for httpx - it's too verbose
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.config:
        config.set_config_path(args.config)

    if args.state:
        state_keeper.set_state_file(args.state)

    host = get_ip_address(config.config["server"]["interface"])
    port = config.config["server"]["port"]
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(
        "src.server:app",
        host=host,
        port=port,
        reload=False,
        timeout_graceful_shutdown=5,
        log_config=uvicorn_log_config,
    )
