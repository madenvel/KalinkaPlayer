"""Hard per-call timeout around input-module interfaces.

The SDK's ``InputModule`` latency contract says every call serves a
real-time request and must finish within the server's per-call budget.
This wrapper is the server-side enforcement: it doesn't matter what the
plugin does — a call that overruns is cancelled and surfaces as a
``TimeoutError``, which the call sites already treat like any other
plugin failure (a failed leg, a dropped shelf, a 500 on /browse).

Wraps every coroutine method transparently; sync attributes pass
through, so ``isinstance(proxy, InputModule)`` (a runtime-checkable
protocol) still holds.
"""

from __future__ import annotations

import asyncio
import functools
import logging

from kalinka_plugin_sdk.inputmodule import InputModule

logger = logging.getLogger(__name__.split(".")[-1])

# Keep in sync with the latency contract stated on the SDK's InputModule
# docstring — plugins size their backend HTTP timeouts against this.
PLUGIN_CALL_TIMEOUT_S = 3.0


class TimeLimitedInputModule:
    """Delegating proxy that applies the per-call budget to every async
    method of ``inner``."""

    def __init__(self, inner: InputModule, label: str,
                 timeout_s: float = PLUGIN_CALL_TIMEOUT_S):
        self._inner = inner
        self._label = label
        self._timeout_s = timeout_s

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not asyncio.iscoroutinefunction(attr):
            return attr

        @functools.wraps(attr)
        async def timed(*args, **kwargs):
            try:
                return await asyncio.wait_for(attr(*args, **kwargs), self._timeout_s)
            except asyncio.TimeoutError:
                logger.warning(
                    "%s.%s exceeded the %.0fs per-call budget",
                    self._label, name, self._timeout_s,
                )
                raise TimeoutError(
                    f"{self._label}.{name} exceeded the "
                    f"{self._timeout_s:.0f}s per-call budget"
                ) from None

        return timed
