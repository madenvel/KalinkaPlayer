"""Single-slot cancellable async operation with a fencing token.

Only one operation runs at a time. Starting a new one — or explicitly
superseding — cancels the previous task and bumps a generation counter. A
late-arriving continuation calls :meth:`is_current` with the generation it was
handed to detect that it was superseded and bail out silently (so it applies no
side effects). :meth:`cancel_if` lets external state changes abort the in-flight
operation when a predicate over its target says they invalidate it.

This pairs with :class:`SerialExecutor`: the slot keeps slow I/O *off* the serial
lane (one cancellable task at a time), while the continuation re-enters the lane
to apply its result, fenced by the generation so a superseded result is dropped.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine, Optional

_CoroFactory = Callable[[int], Coroutine[Any, Any, Any]]


class ResolutionSlot:
    """Holds at most one in-flight async operation, with generation fencing."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._gen: int = 0
        # The target (e.g. a track index) the in-flight operation is working on.
        # Used by cancel_if_affected; None while idle.
        self.target: Optional[int] = None

    @property
    def active(self) -> bool:
        """True while an operation is in flight."""
        return self._task is not None

    def start(self, make_coro: _CoroFactory, target: int) -> int:
        """Cancel any in-flight operation and start a new one.

        ``make_coro`` is called with the new generation token; the coroutine must
        pass it to :meth:`is_current` before applying any side effect, and to
        :meth:`finish` once applied. Returns the new generation.
        """
        self.supersede()
        self._gen += 1
        gen = self._gen
        self.target = target
        self._task = asyncio.create_task(make_coro(gen))
        return gen

    def supersede(self) -> None:
        """Fence any queued continuation and cancel the in-flight task."""
        self._gen += 1
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.target = None

    def cancel_if(self, predicate: Callable[[int], bool]) -> None:
        """Supersede the in-flight operation iff one is running and ``predicate``
        returns True for its target. ``predicate`` is only called when active, so
        callers never have to guard against an absent target."""
        if self._task is not None and predicate(self.target):
            self.supersede()

    def is_current(self, gen: int) -> bool:
        """True if ``gen`` is still the latest generation (not superseded)."""
        return gen == self._gen

    def finish(self, gen: int) -> None:
        """Clear the slot once the current operation has applied its result.

        A no-op if the operation was already superseded, so a stale continuation
        can never clear a newer operation's task.
        """
        if gen == self._gen:
            self._task = None
            self.target = None
