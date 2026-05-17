"""Registry of dynamic-option resolvers for enum-like fields.

A handful of settings have a writable enum choice whose option list
depends on live system state (ALSA devices that come and go on
hot-plug, network interfaces, COM ports, ...). Hard-coding their
choices in the schema would either be stale or churn the
``schema_version`` on every transient hardware change. Instead the
schema flags the field with ``dynamic_options=True`` and emits an
empty ``enum_values``; this module keeps a separate map of
``path -> callable`` whose results are spliced into the
``GET /server/config`` envelope under ``enum_options[path]``.

The resolver is a plain callable (sync or async) that returns a list
of ``OptionSpec`` dicts. Failures are caught at the registry boundary
so a broken enumerator can't take the whole values blob down with it.

Parallel to ``dynamic_field_registry``: that one is for *value*
resolution of read-only status views; this one is for *option*
resolution of writable enum fields. They never overlap.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable, Union

from .presentation_schema import OptionSpec


logger = logging.getLogger(__name__.split(".")[-1])


# A resolver may be sync or async; the registry handles both. It must
# return an iterable of OptionSpec (or anything OptionSpec can absorb).
OptionResolver = Callable[
    [], Union[list[OptionSpec], Awaitable[list[OptionSpec]]]
]


class OptionsRegistry:
    """Process-wide map of dotted config paths to option resolvers.

    Mutable so the server can register entries during startup (after
    plugins are loaded) and consult them per-request. Resolvers
    captured here outlive any single request — they're expected to be
    cheap and side-effect-free, performing only system queries.
    """

    def __init__(self) -> None:
        self._resolvers: dict[str, OptionResolver] = {}

    def register(self, path: str, resolver: OptionResolver) -> None:
        if path in self._resolvers:
            logger.warning(
                "Replacing existing option resolver for %s", path
            )
        self._resolvers[path] = resolver

    def paths(self) -> list[str]:
        return list(self._resolvers)

    async def resolve(self, path: str) -> list[OptionSpec] | None:
        resolver = self._resolvers.get(path)
        if resolver is None:
            return None
        try:
            result = resolver()
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.exception(
                "Option resolver for %s raised; omitting from response: %s",
                path,
                exc,
            )
            return None
        return _coerce_options(result)


def _coerce_options(raw: Any) -> list[OptionSpec]:
    """Accept anything resolver returns (list of OptionSpec, list of
    dicts, list of (value, label) tuples) and normalise.
    """
    out: list[OptionSpec] = []
    for entry in raw or ():
        if isinstance(entry, OptionSpec):
            out.append(entry)
        elif isinstance(entry, dict):
            out.append(OptionSpec(**entry))
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            out.append(OptionSpec(value=str(entry[0]), label=str(entry[1])))
        else:
            logger.warning(
                "Option resolver returned unsupported entry %r; skipping",
                entry,
            )
    return out
