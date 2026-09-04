import abc
import urllib.error
from typing import Dict, List, Optional


class TransientEnrichmentError(Exception):
    """The attempt never reached the service — DNS failure, refused
    connection, timeout. Nothing was learned about the entity, so its row
    must stay pending for the next cycle rather than be recorded. Any HTTP
    response, whatever its status, is the service's verdict and is recorded
    as usual.
    """


def raise_if_musicbrainz_unreachable(error) -> None:
    """Re-raise a ``musicbrainzngs.NetworkError`` as transient unless it
    wraps an HTTP response (MusicBrainz answered — a verdict, not an
    outage)."""
    if not isinstance(getattr(error, "cause", None), urllib.error.HTTPError):
        raise TransientEnrichmentError(
            f"MusicBrainz is unreachable: {error}"
        ) from error


def inferred_claims(source: str, fields: Dict[str, object]) -> List[Dict]:
    """Build the enricher's claim dicts for each non-empty field, attributed to
    ``source`` at the ``inferred`` tier — the shape every plugin emits and the
    enricher resolves. Centralised so the plugin→enricher claim contract lives
    in one place rather than being hand-built per plugin.
    """
    return [
        {"field": field, "value": value, "source": source, "tier": "inferred"}
        for field, value in fields.items()
        if value
    ]


class EnricherPlugin(abc.ABC):
    """Base class for enricher plugins"""

    # Bump in a subclass whenever its enrichment logic changes in a way
    # that could turn a previous FAILED into a success (a new matcher
    # tier, a bug fix, a looser heuristic). It feeds the enrichment
    # fingerprint (see ``MetadataEnricher.compute_fingerprint``); a bump
    # re-opens previously-FAILED rows on the next restart. Scoped per
    # plugin, so bumping a plugin the user has *disabled* changes
    # nothing — it never enters the fingerprint.
    ENRICHER_VERSION: int = 1

    def config_signature(self) -> Dict:
        """Config fields that affect match *outcomes*, folded into the
        enrichment fingerprint so a change re-opens previously-FAILED
        rows on restart.

        Return only fields that can change whether an entity enriches
        successfully (thresholds, an API key becoming available) — not
        cosmetic ones like debug logging. Default: nothing
        outcome-affecting.
        """
        return {}

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
