#!/usr/bin/env python3

import logging
from src.gui_kivy import run_app
from src.player_setup import setup

import logging


def main():
    playqueue, event_listener, trackbrowser = setup()
    categories = trackbrowser.list_categories(
        ["search", "album", "Прости меня моя любовь"], offset=0, limit=10
    )
    categories = trackbrowser.list_categories(categories[0].path, offset=0, limit=100)
    print("Found: ", [category.info["track"].metadata for category in categories])
    # categories = trackbrowser.list_categories(["myweeklyq"])
    playqueue.add(
        [category.info["track"] for category in categories if category.info is not None]
    )

    try:
        run_app(playqueue, event_listener, trackbrowser)
    except Exception as e:
        logging.error("Exception caught:", e)
    finally:
        playqueue.terminate()
        event_listener.terminate()


if __name__ == "__main__":
    main()
