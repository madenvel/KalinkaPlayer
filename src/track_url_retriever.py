from src.trackbrowser import SourceType, TrackInfo
from typing import Callable


class TrackUrlRetriever:
    def __init__(self):
        self.registry = {}

    def register(self, source: SourceType, callback: Callable[[TrackInfo], str]):
        self.registry[source] = callback

    def retrieve(self, info: TrackInfo):
        return self.registry.get(info.track_type, lambda x: None)(info.track_id)
