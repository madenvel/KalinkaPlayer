#!/usr/bin/env python3

import logging
from gui_kivy import run_app

from rpiplayer_http import RpiPlayerHttp
from event_listener_sse import SSEEventListener
import logging

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    player = RpiPlayerHttp(host="192.168.3.28", port=8000)
    event_listener = SSEEventListener(host="192.168.3.28", port=8000)
    event_listener.start()
    try:
        run_app(player, event_listener)
    except Exception as e:
        logging.error("Exception caught:", e)
    finally:
        event_listener.stop()
