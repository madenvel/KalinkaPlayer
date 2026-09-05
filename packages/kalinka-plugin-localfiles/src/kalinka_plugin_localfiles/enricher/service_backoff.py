"""Per-service cooldown after transient enrichment failures.

Skipping a row that a service failed on is only half an answer: when the
service is down, the *next* row fails too, and each failure costs the client's
full internal retry cycle. So a service that fails is stood down for a while —
every plugin backed by it is skipped outright during that window, which lets
the other services keep enriching, and the delay grows while it stays broken
so a long outage costs a handful of probes rather than one per row.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, Optional


class ServiceBackoff:
    """Cooldowns keyed by service name, doubling per consecutive failure.

    ``clock`` is injectable for tests; it must be monotonic.
    """

    def __init__(
        self,
        base_delay: float = 60.0,
        max_delay: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._clock = clock
        self._until: Dict[str, float] = {}
        self._failures: Dict[str, int] = {}

    def is_cooling(self, service: str) -> bool:
        """Whether ``service`` is still standing down."""
        until = self._until.get(service)
        if until is None:
            return False
        if self._clock() >= until:
            del self._until[service]
            return False
        return True

    def record_failure(self, service: str) -> float:
        """Stand ``service`` down; returns the cooldown length in seconds."""
        count = self._failures.get(service, 0) + 1
        self._failures[service] = count
        delay = min(self._base_delay * (2 ** (count - 1)), self._max_delay)
        self._until[service] = self._clock() + delay
        return delay

    def record_success(self, service: str) -> None:
        """Clear a service's cooldown and escalation after it answers."""
        self._failures.pop(service, None)
        self._until.pop(service, None)

    def next_retry_in(self) -> Optional[float]:
        """Seconds until the earliest cooling service may be tried again, or
        None when nothing is standing down. Drives when the enricher wakes to
        retry, instead of waiting for the next library scan."""
        now = self._clock()
        remaining = [until - now for until in self._until.values()]
        if not remaining:
            return None
        return max(0.0, min(remaining))
