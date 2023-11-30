import requests
import logging

from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class RpiPlayerHttp:
    def __init__(self, host: str, port: int = 8000):
        self.address = f"http://{host}:{port}"
        self.session = requests.Session()

    def list(self, offset=0, limit=10):
        return self.session.get(
            urljoin(self.address, "/queue/list"),
            params={"offset": offset, "limit": limit},
        ).json()

    def add_tracks(self, tracks):
        return self.session.post(
            urljoin(self.address, "/queue/add/tracks"), json=tracks
        ).json()

    def add(self, path, replace=False):
        return self.session.get(
            urljoin(self.address, "/queue/add/", path),
            params={"replace": replace},
        ).json()

    def play(self, index=None):
        return self.session.get(
            urljoin(self.address, "/queue/play"), params={"index": index}
        ).json()

    def pause(self, paused=True):
        return self.session.get(
            urljoin(self.address, "/queue/pause"), params={"paused": paused}
        ).json()

    def next(self):
        return self.session.get(urljoin(self.address, "/queue/next")).json()

    def prev(self):
        return self.session.get(urljoin(self.address, "/queue/prev")).json()

    def stop(self):
        return self.session.get(self.address + "/queue/stop").json()

    def browse(self, type, entity_id, offset=0, limit=20):
        return self.session.get(
            urljoin(self.address, "/browse/" + type + "/" + entity_id),
            params={"offset": offset, "limit": limit},
        ).json()

    def browse(self, path, offset=0, limit=20):
        logger.info("Browsing " + path)
        return self.session.get(
            urljoin(self.address, path), params={"offset": offset, "limit": limit}
        ).json()

    def search(self, search_type, query, offset=0, limit=20):
        return self.session.get(
            urljoin(self.address, "/search/" + search_type + "/" + query),
            params={"offset": offset, "limit": limit},
        ).json()

    def get_state(self):
        return self.session.get(urljoin(self.address, "/queue/state")).json()

    def set_pos(self, pos):
        pass
