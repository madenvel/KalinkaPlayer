"""Where a pass spent its time, per enrichment source.

Which source is slow cannot be found by disabling one and comparing: the
chain stops as soon as an entity has every desired field, so removing a
plugin changes how often the remaining ones run. Measuring each call
instead leaves the pipeline untouched, and one pass answers the question
that a bisect would need a run per plugin to approximate.

Failed calls are counted too — a source that costs a minute to time out is
exactly the one worth finding.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Callable, Dict, Iterator, List, Tuple


class ServiceTimings:
    """Call count and total wall time per service, for one enrichment pass.

    ``clock`` is injectable for tests; it must be monotonic.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._calls: Dict[str, int] = {}
        self._seconds: Dict[str, float] = {}

    @contextmanager
    def measure(self, service: str) -> Iterator[None]:
        """Time the wrapped call against ``service``, raise or not."""
        started = self._clock()
        try:
            yield
        finally:
            self.record(service, self._clock() - started)

    def record(self, service: str, elapsed: float) -> None:
        """Add one call of ``elapsed`` seconds to ``service``'s tally."""
        self._calls[service] = self._calls.get(service, 0) + 1
        self._seconds[service] = self._seconds.get(service, 0.0) + elapsed

    def reset(self) -> None:
        """Drop every tally, so the next pass reports only its own time."""
        self._calls.clear()
        self._seconds.clear()

    @property
    def total_seconds(self) -> float:
        """Wall time across every service. Concurrent workers overlap, so
        this exceeds the pass's own duration and is only a share basis."""
        return sum(self._seconds.values())

    def summary(self) -> List[Tuple[str, int, float]]:
        """``(service, calls, seconds)`` worst first, for the pass report."""
        rows = [
            (service, self._calls[service], seconds)
            for service, seconds in self._seconds.items()
        ]
        rows.sort(key=lambda row: row[2], reverse=True)
        return rows

    def __bool__(self) -> bool:
        return bool(self._calls)
