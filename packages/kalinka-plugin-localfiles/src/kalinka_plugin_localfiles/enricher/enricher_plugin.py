import abc
from typing import Dict, Optional


class EnricherPlugin(abc.ABC):
    """Base class for enricher plugins"""

    @abc.abstractmethod
    def can_enrich_artist(self) -> bool:
        """Whether this plugin can enrich artist metadata"""
        pass

    @abc.abstractmethod
    def can_enrich_album(self) -> bool:
        """Whether this plugin can enrich album metadata"""
        pass

    @abc.abstractmethod
    def can_enrich_track(self) -> bool:
        """Whether this plugin can enrich track metadata"""
        pass

    @abc.abstractmethod
    async def enrich_artist(self, artist: Dict) -> Optional[Dict]:
        """Enrich artist metadata"""
        pass

    @abc.abstractmethod
    async def enrich_album(self, album: Dict) -> Optional[Dict]:
        """Enrich album metadata"""
        pass

    @abc.abstractmethod
    async def enrich_track(self, track: Dict) -> Optional[Dict]:
        """Enrich track metadata"""
        pass
