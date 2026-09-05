import abc
from typing import Dict, List, Optional


class TransientEnrichmentError(Exception):
    """The service never gave a verdict about the entity — DNS failure,
    refused connection, timeout, or overload/rate-limit responses. Nothing
    was learned about the entity, so its row must stay pending for the next
    cycle rather than be recorded. A response that speaks about the entity
    (found, 404, …) is a verdict and is recorded as usual.
    """


def raise_musicbrainz_unreachable(error) -> None:
    """Re-raise a ``musicbrainzngs.NetworkError`` as transient. Verdicts
    (400/404/411) arrive as ``ResponseError``; a NetworkError wrapping an
    HTTP response can only be 5xx that survived the library's own retries —
    MusicBrainz's rate-limit/overload signal, never a statement about the
    entity."""
    raise TransientEnrichmentError(
        f"MusicBrainz is unreachable: {error}"
    ) from error


def raise_if_service_unavailable(status_code: int, service: str) -> None:
    """Turn a throttle or server-side failure into a transient error.

    429 and 5xx describe the service's own state, not the entity: recording
    them as a verdict marks the row terminal for an outage it knew nothing
    about. Every other status — 200, 404, a malformed request — is the
    service answering about the entity and stays the caller's to interpret.
    """
    if status_code == 429 or status_code >= 500:
        raise TransientEnrichmentError(
            f"{service} is unavailable: HTTP {status_code}"
        )


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
