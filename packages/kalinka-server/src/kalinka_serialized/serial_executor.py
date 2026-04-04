"""
Serial executor with priority interrupt lanes and deadlock detection.

Provides:
  - SerialExecutor: asyncio runner with normal and priority FIFO queues
  - @serialised: method decorator routing through the normal lane
  - @interrupt: method decorator routing through the priority lane
  - @with_serial_executor: class decorator that injects a SerialExecutor
  - StateWatcher: fires @interrupt handlers for each state from an async monitor
  - DeadlockError: raised on in-executor re-entrancy
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
from collections import deque
from typing import Any, AsyncIterator, Callable, Coroutine, TypeVar

_T = TypeVar("_T")


# ---------------------------------------------------------------------------
# DeadlockError
# ---------------------------------------------------------------------------


class DeadlockError(RuntimeError):
    """Raised when a serialised coroutine submits and awaits another serialised
    coroutine on the same executor, which would deadlock indefinitely."""


# ---------------------------------------------------------------------------
# Internal work item
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _WorkItem:
    coro: Coroutine[Any, Any, Any]
    future: asyncio.Future[Any]


# ---------------------------------------------------------------------------
# SerialExecutor
# ---------------------------------------------------------------------------


class SerialExecutor:
    """Asyncio-based serial executor with a priority interrupt lane.

    Normal items execute in FIFO order.  Priority (interrupt) items are
    always drained before any normal item is picked up.  While a priority
    coroutine is executing the *_no_interrupt* event is cleared, acting as a
    CPU-style interrupt flag that blocks normal work from being picked up.
    """

    def __init__(self) -> None:
        self._normal: deque[_WorkItem] = deque()
        self._priority: deque[_WorkItem] = deque()
        # set = no interrupt active; clear = a priority item is executing
        self._no_interrupt: asyncio.Event = asyncio.Event()
        # set = there is work in at least one queue
        self._work_event: asyncio.Event = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None
        # Points to _runner_task while a coroutine is being executed inside it.
        # Used for deadlock detection: if the submitter IS this task we would
        # wait forever for ourselves to finish.
        self._executing_task: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the runner task.  Must be called from within a running loop."""
        self._no_interrupt.set()
        loop = asyncio.get_running_loop()
        self._runner_task = loop.create_task(self._run(), name="SerialExecutor._run")

    async def stop(self) -> None:
        """Drain both queues, then cancel the runner task."""
        if self._runner_task is None:
            return

        # Inject a no-op sentinel at the end of the normal queue.
        # Because priority always drains first, by the time the sentinel
        # executes all currently-queued items (both lanes) have been processed.
        loop = asyncio.get_running_loop()
        drain_future: asyncio.Future[None] = loop.create_future()

        async def _sentinel() -> None:
            pass

        self._normal.append(_WorkItem(coro=_sentinel(), future=drain_future))
        self._work_event.set()
        await drain_future

        self._runner_task.cancel()
        try:
            await self._runner_task
        except asyncio.CancelledError:
            pass
        self._runner_task = None

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    async def submit(
        self,
        coro: Coroutine[Any, Any, _T],
        priority: bool = False,
    ) -> _T:
        """Queue *coro* and suspend the caller until it finishes.

        Raises DeadlockError immediately if called from within the currently
        executing serialised coroutine (re-entrancy deadlock).
        """
        current = asyncio.current_task()
        if current is not None and current is self._executing_task:
            # Close the coroutine to avoid "coroutine was never awaited" warning.
            coro.close()
            raise DeadlockError(
                "Deadlock detected: a serialised coroutine on this executor "
                "attempted to submit and await another serialised coroutine. "
                "Only entry-point methods should be decorated; internal helpers "
                "must not be decorated."
            )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        item = _WorkItem(coro=coro, future=future)

        if priority:
            self._priority.append(item)
        else:
            self._normal.append(item)

        self._work_event.set()
        return await future  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Internal runner
    # ------------------------------------------------------------------

    async def _execute(self, item: _WorkItem) -> None:
        """Run one work item, routing result/exception to its future."""
        assert self._runner_task is not None
        self._executing_task = self._runner_task
        try:
            result = await item.coro
            if not item.future.done():
                item.future.set_result(result)
        except Exception as exc:
            if not item.future.done():
                item.future.set_exception(exc)
        finally:
            self._executing_task = None

    async def _run(self) -> None:
        """Main runner loop: priority-first, then normal, with re-check."""
        while True:
            if self._priority:
                item = self._priority.popleft()
                self._no_interrupt.clear()
                await self._execute(item)
                self._no_interrupt.set()

            elif self._normal:
                # Yield to the event loop here.  This is the point where a
                # priority submission can "win the race": after the await,
                # _priority may be non-empty.
                await self._no_interrupt.wait()
                if self._priority:
                    # A priority item arrived while we yielded — re-check.
                    continue
                item = self._normal.popleft()
                await self._execute(item)

            else:
                # Both queues empty — wait for new work.
                # Clear the event *before* the await so we never miss a
                # wakeup: any submit() that runs after the clear will set it.
                self._work_event.clear()
                # Synchronous double-check (no interleave possible here).
                if self._priority or self._normal:
                    continue
                await self._work_event.wait()


# ---------------------------------------------------------------------------
# @serialised decorator
# ---------------------------------------------------------------------------


def serialised(method: Callable[..., Coroutine[Any, Any, _T]]) -> Callable[..., Coroutine[Any, Any, _T]]:
    """Route an async method through self._executor (normal lane)."""

    @functools.wraps(method)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> _T:
        return await self._executor.submit(method(self, *args, **kwargs), priority=False)

    return wrapper  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# @interrupt decorator
# ---------------------------------------------------------------------------


def interrupt(method: Callable[..., Coroutine[Any, Any, _T]]) -> Callable[..., Coroutine[Any, Any, _T]]:
    """Route an async method through self._executor (priority/interrupt lane).

    Preempts all queued normal work: inserted into the priority lane and
    blocks normal execution until complete.  Multiple interrupts are still
    serialised with each other (FIFO within the priority queue).
    """

    @functools.wraps(method)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> _T:
        return await self._executor.submit(method(self, *args, **kwargs), priority=True)

    return wrapper  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# @with_serial_executor class decorator
# ---------------------------------------------------------------------------


def with_serial_executor(cls: type) -> type:
    """Inject a per-instance SerialExecutor without requiring inheritance.

    Wraps __init__ to:
      1. create self._executor = SerialExecutor()
      2. run the original __init__
      3. call self._executor.start()

    The original class object is returned unchanged (name, module, docstring
    and all other class attributes are preserved automatically).
    """
    original_init = cls.__init__  # type: ignore[misc]

    @functools.wraps(original_init)
    def _new_init(self: Any, *args: Any, **kwargs: Any) -> None:
        self._executor = SerialExecutor()
        original_init(self, *args, **kwargs)
        self._executor.start()

    cls.__init__ = _new_init  # type: ignore[misc]
    return cls


# ---------------------------------------------------------------------------
# StateWatcher
# ---------------------------------------------------------------------------


class StateWatcher:
    """Watches an async state monitor and fires an interrupt handler per state.

    The handler is fired via asyncio.ensure_future (fire-and-forget) so the
    monitor loop is never blocked by handler execution or queue depth.
    """

    def __init__(
        self,
        monitor: AsyncIterator[Any],
        handler: Callable[[Any], Coroutine[Any, Any, Any]],
    ) -> None:
        self._monitor = monitor
        self._handler = handler

    async def run(self) -> None:
        """Consume states from the monitor and fire the handler for each."""
        async for state in self._monitor:
            asyncio.ensure_future(self._handler(state))


# ---------------------------------------------------------------------------
# __main__ demonstration
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import sys

    async def _demo() -> None:
        print("=== 1. Serial execution via asyncio.gather ===")

        @with_serial_executor
        class Actor:
            """Demo actor."""

            execution_log: list[str]

            def __init__(self) -> None:
                self.execution_log = []

            @serialised
            async def work(self, name: str, delay: float) -> str:
                self.execution_log.append(f"start:{name}")
                await asyncio.sleep(delay)
                self.execution_log.append(f"end:{name}")
                return f"result:{name}"

            @interrupt
            async def on_interrupt(self, signal: str) -> str:
                self.execution_log.append(f"interrupt:{signal}")
                return f"ack:{signal}"

            @serialised
            async def will_deadlock(self) -> None:
                # Calling another @serialised method from inside a @serialised
                # method is detected immediately.
                await self.will_deadlock()

        actor = Actor()

        # Submit several @serialised calls concurrently.
        tasks = [
            asyncio.ensure_future(actor.work(f"task{i}", 0.05))
            for i in range(4)
        ]
        results = await asyncio.gather(*tasks)
        print(f"  results : {results}")
        print(f"  log     : {actor.execution_log}")
        # Verify no interleaving: every start is immediately followed by its end.
        for i in range(4):
            si = actor.execution_log.index(f"start:task{i}")
            ei = actor.execution_log.index(f"end:task{i}")
            assert ei == si + 1, f"task{i} interleaved!"
        print("  -> No interleaving confirmed.\n")

        print("=== 2. @interrupt arrives mid-queue ===")
        actor2 = Actor()
        normal_tasks = [
            asyncio.ensure_future(actor2.work(f"w{i}", 0.05))
            for i in range(3)
        ]
        # Yield once so the first normal task starts executing.
        await asyncio.sleep(0)
        int_task = asyncio.ensure_future(actor2.on_interrupt("PRIORITY"))
        await asyncio.gather(*normal_tasks, int_task)
        print(f"  log: {actor2.execution_log}")
        # PRIORITY must appear before w1 and w2 start (they were queued, not started).
        int_pos = actor2.execution_log.index("interrupt:PRIORITY")
        w1_start = actor2.execution_log.index("start:w1")
        assert int_pos < w1_start, "Interrupt did not preempt normal queue!"
        print("  -> Interrupt preempted queued normal work.\n")

        print("=== 3. DeadlockError detection ===")
        actor3 = Actor()
        try:
            await actor3.will_deadlock()
            print("  ERROR: no exception raised!")
            sys.exit(1)
        except DeadlockError as exc:
            print(f"  Caught DeadlockError: {exc}\n")

        print("=== 4. StateWatcher driving @interrupt calls ===")

        class _MockMonitor:
            def __init__(self, states: list[str]) -> None:
                self._states = iter(states)

            def __aiter__(self) -> "_MockMonitor":
                return self

            async def __anext__(self) -> str:
                try:
                    await asyncio.sleep(0.02)
                    return next(self._states)
                except StopIteration:
                    raise StopAsyncIteration

        actor4 = Actor()
        monitor = _MockMonitor(["alpha", "beta", "gamma"])
        watcher = StateWatcher(monitor, actor4.on_interrupt)
        watcher_task = asyncio.ensure_future(watcher.run())
        await asyncio.sleep(0.2)
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass
        print(f"  log: {actor4.execution_log}")
        assert actor4.execution_log == [
            "interrupt:alpha",
            "interrupt:beta",
            "interrupt:gamma",
        ], f"Unexpected log: {actor4.execution_log}"
        print("  -> StateWatcher fired all interrupt handlers.\n")

        await actor._executor.stop()
        await actor2._executor.stop()
        await actor3._executor.stop()
        await actor4._executor.stop()
        print("All demos passed.")

    asyncio.run(_demo())
