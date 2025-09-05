#!/usr/bin/env python3
from asyncio import CancelledError
import json
import logging
import os
import uvicorn

from src import state_keeper
from src.config_model import KalinkaConfig
from src.netutils import get_ip_address

import argparse

from src.server import create_app


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
        default="kalinka_conf.cfg",
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


def main():
    """Main entry point for the Kalinka server."""
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug is True else logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(thread)d %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Reduce logging level for httpx - it's too verbose
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:

        while True:
            config = KalinkaConfig()
            try:
                with open(args.config, "r") as f:
                    config = KalinkaConfig(**json.load(f))
            except Exception as e:
                logger.warning(
                    f"Config file {args.config} not found or corrupted. Using default configuration."
                )

            if args.state:
                state_keeper.set_state_file(args.state)

            host = get_ip_address(config.server.interface)
            port = config.server.port
            logger.info(f"Starting server on {host}:{port}")
            app = create_app(args.config, config)
            uvicorn_config = uvicorn.Config(
                app,
                host=host,
                port=port,
                reload=False,
                timeout_graceful_shutdown=5,
                log_config=uvicorn_log_config,
            )
            server = uvicorn.Server(uvicorn_config)
            app.state.server = server
            server.run()
            if server.should_exit:
                logger.info("Server restarting ...")
            else:
                logger.info("Server shut down")
                break

    except KeyboardInterrupt:
        logger.info("Server shut down")
    except CancelledError:
        logger.info("Server shut down")
    except Exception as e:
        logger.error(f"Error starting server: {e}")
        raise


if __name__ == "__main__":
    main()
