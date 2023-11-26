from abc import ABC, abstractmethod
from json import JSONEncoder


class TrackInfo:
    def __init__(self, metadata: dict, link_retriever, id: dict = None):
        self.metadata = metadata
        self.link_retriever = link_retriever
        self.id = id


class TrackUrl:
    def __init__(
        self,
        url: str,
        format: str = None,
        sample_rate: int = None,
        bit_depth: int = None,
    ):
        self.format = format
        self.sample_rate = sample_rate
        self.bit_depth = bit_depth
        self.url = url


class BrowseCategory:
    def __init__(
        self,
        id: str,
        name: str,
        can_browse: bool,
        needs_input: bool = False,
        info: dict = {},
        path: [str] = [],
        image: dict = {},
    ):
        self.id = id
        self.name = name
        self.can_browse = can_browse
        self.needs_input = needs_input
        self.info = info
        self.path = path


class TrackBrowser(ABC):
    @abstractmethod
    def list_categories(
        self,
        path: list[str],
        offset=0,
        limit=50,
    ) -> list[BrowseCategory]:
        pass
