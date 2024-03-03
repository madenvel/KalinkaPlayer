#!/usr/bin/env python3
import logging
import uvicorn

from src.config import config
from src.netutils import get_ip_address

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    host = get_ip_address(config["server"]["interface"])
    port = config["server"]["port"]
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(
        "src.server:app",
        host=host,
        port=port,
        reload=False,
    )
