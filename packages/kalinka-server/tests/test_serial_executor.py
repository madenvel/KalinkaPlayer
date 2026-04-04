"""Extensive tests for kalinka_serialized.serial_executor."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest

from kalinka_serialized import (
    DeadlockError,
    SerialExecutor,
    StateWatcher,
    interrupt,
    serialised,
    with_serial_executor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_executor() -> SerialExecutor:
    ex = SerialExecutor()
    ex.start()
    return ex


async def _recording_coro(order: list[str], name: str, delay: float = 0.0) -> str:
    order.append(f"start:{name}")
    if delay:
        await asyncio.sleep(delay)
    order.append(f"end:{name}")
    return name


# ---------------------------------------------------------------------------
# TestSerialExecutorDirect – raw SerialExecutor API
# ---------------------------------------------------------------------------


class TestSerialExecutorDirect:
    async def test_fifo_order(self) -> None:
        ex = await _make_executor()
        order: list[str] = []
        tasks = [
            asyncio.ensure_future(
                ex.submit(_recording_coro(order, str(i), delay=0.01))
            )
            for i in range(5)
        ]
        await asyncio.gather(*tasks)
        starts = [e for e in order if e.startswith("start:")]
        assert starts == [f"start:{i}" for i in range(5)], "FIFO violated"
        await ex.stop()

    async def test_result_returned(self) -> None:
        ex = await _make_executor()

        async def _ret() -> int:
            return 42

        assert await ex.submit(_ret()) == 42
        await ex.stop()

    async def test_exception_propagated(self) -> None:
        ex = await _make_executor()

        async def _boom() -> None:
            raise ValueError("oops")

        with pytest.raises(ValueError, match="oops"):
            await ex.submit(_boom())
        await ex.stop()

    async def test_concurrent_submitters_are_serialised(self) -> None:
        """Four tasks all submit at the same time; executions must not overlap."""
        ex = await _make_executor()
        running: list[str] = []
        overlap_detected = False

        async def _task(name: str) -> None:
            nonlocal overlap_detected
            running.append(name)
            if len(running) > 1:
                overlap_detected = True
            await asyncio.sleep(0.02)
            running.remove(name)

        coros = [ex.submit(_task(f"t{i}")) for i in range(4)]
        await asyncio.gather(*coros)
        assert not overlap_detected, "Coroutines ran concurrently"
        await ex.stop()

    async def test_no_interleaving(self) -> None:
        """start:X must be immediately followed by end:X (no other entry between)."""
        ex = await _make_executor()
        order: list[str] = []
        await asyncio.gather(
            *[ex.submit(_recording_coro(order, f"j{i}", delay=0.01)) for i in range(4)]
        )
        for i in range(4):
            si = order.index(f"start:j{i}")
            ei = order.index(f"end:j{i}")
            assert ei == si + 1, f"j{i} interleaved: {order}"
        await ex.stop()


# ---------------------------------------------------------------------------
# TestPriorityLane
# ---------------------------------------------------------------------------


class TestPriorityLane:
    async def test_priority_runs_before_queued_normal(self) -> None:
        """Priority item inserted mid-queue runs before remaining normal items."""
        ex = await _make_executor()
        order: list[str] = []

        # Queue three normal items; first one starts immediately.
        n_tasks = [
            asyncio.ensure_future(
                ex.submit(_recording_coro(order, f"n{i}", delay=0.05))
            )
            for i in range(3)
        ]
        # Let n0 start executing.
        await asyncio.sleep(0.01)

        # Submit a priority item — should run before n1 and n2.
        p_task = asyncio.ensure_future(
            ex.submit(_recording_coro(order, "P"), priority=True)
        )
        await asyncio.gather(*n_tasks, p_task)

        p_pos = order.index("start:P")
        n1_pos = order.index("start:n1")
        n2_pos = order.index("start:n2")
        assert p_pos < n1_pos and p_pos < n2_pos, (
            f"Priority did not preempt: {order}"
        )
        await ex.stop()

    async def test_multiple_priority_items_fifo(self) -> None:
        """Multiple priority items run in FIFO order among themselves."""
        ex = await _make_executor()
        order: list[str] = []

        # Block the executor with a slow normal item.
        blocker = asyncio.ensure_future(
            ex.submit(_recording_coro(order, "block", delay=0.1))
        )
        await asyncio.sleep(0.01)  # Let it start.

        p_tasks = [
            asyncio.ensure_future(
                ex.submit(_recording_coro(order, f"p{i}"), priority=True)
            )
            for i in range(3)
        ]
        await asyncio.gather(blocker, *p_tasks)

        p_starts = [order.index(f"start:p{i}") for i in range(3)]
        assert p_starts == sorted(p_starts), f"Priority FIFO violated: {order}"
        await ex.stop()

    async def test_priority_drains_fully_before_normal(self) -> None:
        """When both queues are non-empty, all priority runs before any normal."""
        ex = await _make_executor()
        order: list[str] = []

        # Block executor.
        blocker = asyncio.ensure_future(
            ex.submit(_recording_coro(order, "block", delay=0.05))
        )
        await asyncio.sleep(0.01)

        # Queue normal and priority items while executor is busy.
        n_tasks = [
            asyncio.ensure_future(ex.submit(_recording_coro(order, f"n{i}")))
            for i in range(3)
        ]
        p_tasks = [
            asyncio.ensure_future(
                ex.submit(_recording_coro(order, f"p{i}"), priority=True)
            )
            for i in range(3)
        ]
        await asyncio.gather(blocker, *n_tasks, *p_tasks)

        # All priority starts must precede any normal start (after "block").
        post_block = order[order.index("end:block") + 1 :]
        p_indices = [post_block.index(f"start:p{i}") for i in range(3)]
        n_indices = [post_block.index(f"start:n{i}") for i in range(3)]
        assert max(p_indices) < min(n_indices), (
            f"Normal ran before priority drained: {post_block}"
        )
        await ex.stop()

    async def test_interrupt_flag_cleared_during_priority(self) -> None:
        """_no_interrupt event is cleared while a priority item executes."""
        ex = await _make_executor()
        snapshots: list[bool] = []

        async def _check_flag() -> None:
            snapshots.append(ex._no_interrupt.is_set())
            await asyncio.sleep(0.02)
            snapshots.append(ex._no_interrupt.is_set())

        await ex.submit(_check_flag(), priority=True)
        # During priority execution the flag must have been cleared (False).
        assert snapshots[0] is False, "Flag not cleared at start of priority item"
        # After priority finishes the flag is set again.
        assert ex._no_interrupt.is_set(), "Flag not restored after priority item"
        await ex.stop()

    async def test_interrupt_flag_set_during_normal(self) -> None:
        """_no_interrupt event remains set while a normal item executes."""
        ex = await _make_executor()
        snapshots: list[bool] = []

        async def _check_flag() -> None:
            snapshots.append(ex._no_interrupt.is_set())

        await ex.submit(_check_flag(), priority=False)
        assert snapshots[0] is True, "Flag should be set during normal item"
        await ex.stop()


# ---------------------------------------------------------------------------
# TestDeadlock
# ---------------------------------------------------------------------------


class TestDeadlock:
    async def test_serialised_awaiting_serialised_raises_immediately(self) -> None:
        """Re-entrant submit raises DeadlockError, not a hang."""
        ex = await _make_executor()

        async def _outer() -> None:
            async def _inner() -> None:
                pass

            await ex.submit(_inner())  # This is a re-entrant call → DeadlockError

        with pytest.raises(DeadlockError):
            await ex.submit(_outer())

        # Executor must still be usable after the deadlock.
        result = await ex.submit(asyncio.coroutine(lambda: 99)() if False else _noop_returning(99))
        assert result == 99
        await ex.stop()

    async def test_interrupt_awaiting_serialised_raises_immediately(self) -> None:
        """Re-entrant submit from priority lane also raises DeadlockError."""
        ex = await _make_executor()

        async def _outer() -> None:
            async def _inner() -> None:
                pass

            await ex.submit(_inner())

        with pytest.raises(DeadlockError):
            await ex.submit(_outer(), priority=True)

        await ex.stop()

    async def test_external_caller_no_deadlock(self) -> None:
        """A task external to the executor can submit while it is busy."""
        ex = await _make_executor()
        results: list[int] = []

        async def _slow() -> None:
            await asyncio.sleep(0.05)
            results.append(1)

        async def _fast() -> None:
            results.append(2)

        t1 = asyncio.ensure_future(ex.submit(_slow()))
        await asyncio.sleep(0.01)
        t2 = asyncio.ensure_future(ex.submit(_fast()))
        await asyncio.gather(t1, t2)
        assert results == [1, 2]
        await ex.stop()

    async def test_deadlock_error_message_is_informative(self) -> None:
        ex = await _make_executor()

        async def _outer() -> None:
            async def _inner() -> None:
                pass
            await ex.submit(_inner())

        with pytest.raises(DeadlockError) as exc_info:
            await ex.submit(_outer())

        assert "Deadlock" in str(exc_info.value)
        await ex.stop()

    async def test_executor_usable_after_deadlock(self) -> None:
        """After a DeadlockError the executor must continue processing."""
        ex = await _make_executor()

        async def _outer() -> None:
            async def _inner() -> None:
                pass
            await ex.submit(_inner())

        with pytest.raises(DeadlockError):
            await ex.submit(_outer())

        assert await ex.submit(_noop_returning(7)) == 7
        await ex.stop()


async def _noop_returning(value: Any) -> Any:
    return value


# ---------------------------------------------------------------------------
# TestWithSerialExecutorDecorator
# ---------------------------------------------------------------------------


class TestWithSerialExecutorDecorator:
    async def test_executor_attribute_injected(self) -> None:
        @with_serial_executor
        class Foo:
            pass

        f = Foo()
        assert hasattr(f, "_executor")
        assert isinstance(f._executor, SerialExecutor)
        await f._executor.stop()

    async def test_class_name_preserved(self) -> None:
        @with_serial_executor
        class MySpecialClass:
            pass

        assert MySpecialClass.__name__ == "MySpecialClass"

    async def test_class_module_preserved(self) -> None:
        @with_serial_executor
        class Foo:
            pass

        assert Foo.__module__ == __name__

    async def test_class_docstring_preserved(self) -> None:
        @with_serial_executor
        class Documented:
            """This is the docstring."""

        assert Documented.__doc__ == "This is the docstring."

    async def test_init_args_work(self) -> None:
        @with_serial_executor
        class Counter:
            def __init__(self, start: int) -> None:
                self.value = start

        c = Counter(10)
        assert c.value == 10
        await c._executor.stop()

    async def test_per_instance_executor(self) -> None:
        @with_serial_executor
        class Foo:
            pass

        a, b = Foo(), Foo()
        assert a._executor is not b._executor
        await a._executor.stop()
        await b._executor.stop()

    async def test_executor_started_on_init(self) -> None:
        @with_serial_executor
        class Foo:
            pass

        f = Foo()
        assert f._executor._runner_task is not None
        assert not f._executor._runner_task.done()
        await f._executor.stop()

    async def test_no_base_class_required(self) -> None:
        """The decorator must not require any specific base class."""

        @with_serial_executor
        class Plain:
            pass

        assert Plain.__bases__ == (object,)
        p = Plain()
        await p._executor.stop()


# ---------------------------------------------------------------------------
# TestSerialisedDecorator
# ---------------------------------------------------------------------------


class TestSerialisedDecorator:
    async def test_methods_serialised(self) -> None:
        @with_serial_executor
        class Actor:
            def __init__(self) -> None:
                self.log: list[str] = []

            @serialised
            async def act(self, name: str) -> None:
                self.log.append(f"start:{name}")
                await asyncio.sleep(0.02)
                self.log.append(f"end:{name}")

        a = Actor()
        await asyncio.gather(*[a.act(f"m{i}") for i in range(4)])
        for i in range(4):
            si = a.log.index(f"start:m{i}")
            ei = a.log.index(f"end:m{i}")
            assert ei == si + 1, f"m{i} interleaved: {a.log}"
        await a._executor.stop()

    async def test_return_value(self) -> None:
        @with_serial_executor
        class Calc:
            @serialised
            async def double(self, x: int) -> int:
                return x * 2

        c = Calc()
        assert await c.double(21) == 42
        await c._executor.stop()

    async def test_exception_propagation(self) -> None:
        @with_serial_executor
        class Boom:
            @serialised
            async def explode(self) -> None:
                raise RuntimeError("bang")

        b = Boom()
        with pytest.raises(RuntimeError, match="bang"):
            await b.explode()
        await b._executor.stop()

    async def test_preserves_method_name(self) -> None:
        @with_serial_executor
        class Foo:
            @serialised
            async def my_method(self) -> None:
                pass

        assert Foo.my_method.__name__ == "my_method"


# ---------------------------------------------------------------------------
# TestInterruptDecorator
# ---------------------------------------------------------------------------


class TestInterruptDecorator:
    async def test_interrupt_preempts_queued_serialised(self) -> None:
        @with_serial_executor
        class Reactor:
            def __init__(self) -> None:
                self.log: list[str] = []

            @serialised
            async def normal(self, name: str) -> None:
                self.log.append(f"start:{name}")
                await asyncio.sleep(0.05)
                self.log.append(f"end:{name}")

            @interrupt
            async def priority(self, name: str) -> None:
                self.log.append(f"interrupt:{name}")

        r = Reactor()
        n_tasks = [asyncio.ensure_future(r.normal(f"n{i}")) for i in range(3)]
        await asyncio.sleep(0.01)  # Let n0 start.
        p_task = asyncio.ensure_future(r.priority("P"))
        await asyncio.gather(*n_tasks, p_task)

        p_pos = r.log.index("interrupt:P")
        n1_start = r.log.index("start:n1")
        assert p_pos < n1_start, f"Interrupt did not preempt: {r.log}"
        await r._executor.stop()

    async def test_interrupt_return_value(self) -> None:
        @with_serial_executor
        class Foo:
            @interrupt
            async def ping(self) -> str:
                return "pong"

        f = Foo()
        assert await f.ping() == "pong"
        await f._executor.stop()

    async def test_multiple_interrupts_fifo(self) -> None:
        @with_serial_executor
        class Foo:
            def __init__(self) -> None:
                self.log: list[str] = []

            @serialised
            async def slow(self) -> None:
                await asyncio.sleep(0.1)

            @interrupt
            async def signal(self, name: str) -> None:
                self.log.append(name)

        f = Foo()
        blocker = asyncio.ensure_future(f.slow())
        await asyncio.sleep(0.01)
        p_tasks = [asyncio.ensure_future(f.signal(f"p{i}")) for i in range(4)]
        await asyncio.gather(blocker, *p_tasks)
        assert f.log == [f"p{i}" for i in range(4)], f"FIFO violated: {f.log}"
        await f._executor.stop()

    async def test_deadlock_from_interrupt(self) -> None:
        @with_serial_executor
        class Foo:
            @interrupt
            async def outer(self) -> None:
                await self.outer()  # re-entrant interrupt

        f = Foo()
        with pytest.raises(DeadlockError):
            await f.outer()
        await f._executor.stop()

    async def test_interrupt_preserves_method_name(self) -> None:
        @with_serial_executor
        class Foo:
            @interrupt
            async def handle_event(self) -> None:
                pass

        assert Foo.handle_event.__name__ == "handle_event"


# ---------------------------------------------------------------------------
# TestStateWatcher
# ---------------------------------------------------------------------------


class _IterMonitor:
    """Async iterator that yields from a list with optional per-item delay."""

    def __init__(self, states: list[Any], delay: float = 0.0) -> None:
        self._states = iter(states)
        self._delay = delay

    def __aiter__(self) -> "_IterMonitor":
        return self

    async def __anext__(self) -> Any:
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            return next(self._states)
        except StopIteration:
            raise StopAsyncIteration


class TestStateWatcher:
    async def test_fires_handler_for_each_state(self) -> None:
        received: list[str] = []

        async def handler(state: str) -> None:
            received.append(state)

        monitor = _IterMonitor(["a", "b", "c"])
        watcher = StateWatcher(monitor, handler)
        await watcher.run()
        # All tasks launched via ensure_future may not have run yet.
        await asyncio.sleep(0)
        assert received == ["a", "b", "c"]

    async def test_does_not_block_monitor_loop(self) -> None:
        """A slow handler must not stall the monitor's async-for loop."""
        monitor_iterations: list[str] = []
        handler_done: list[str] = []

        async def slow_handler(state: str) -> None:
            await asyncio.sleep(0.1)  # slow
            handler_done.append(state)

        class _TrackingMonitor:
            def __init__(self) -> None:
                self._states = iter(["x", "y", "z"])

            def __aiter__(self) -> "_TrackingMonitor":
                return self

            async def __anext__(self) -> str:
                try:
                    val = next(self._states)
                    monitor_iterations.append(val)
                    return val
                except StopIteration:
                    raise StopAsyncIteration

        watcher = StateWatcher(_TrackingMonitor(), slow_handler)
        await watcher.run()
        # All three monitor iterations complete immediately (no blocking).
        assert monitor_iterations == ["x", "y", "z"]
        # Handlers haven't finished yet (they're slow and fire-and-forget).
        assert handler_done == [], "Handler should not have blocked monitor loop"

    async def test_fire_and_forget_tasks_eventually_complete(self) -> None:
        received: list[int] = []

        async def handler(state: int) -> None:
            await asyncio.sleep(0.01)
            received.append(state)

        monitor = _IterMonitor([1, 2, 3])
        watcher = StateWatcher(monitor, handler)
        await watcher.run()
        await asyncio.sleep(0.1)  # Give fire-and-forget tasks time to complete.
        assert sorted(received) == [1, 2, 3]

    async def test_state_watcher_with_interrupt_handler(self) -> None:
        """StateWatcher driving an @interrupt method on a decorated class."""

        @with_serial_executor
        class Device:
            def __init__(self) -> None:
                self.states: list[str] = []

            @interrupt
            async def on_state(self, state: str) -> None:
                self.states.append(state)

        dev = Device()
        monitor = _IterMonitor(["on", "off", "on"], delay=0.01)
        watcher = StateWatcher(monitor, dev.on_state)
        await watcher.run()
        await asyncio.sleep(0.2)
        assert dev.states == ["on", "off", "on"]
        await dev._executor.stop()

    async def test_handler_exceptions_do_not_crash_watcher(self) -> None:
        """An exception in a fire-and-forget handler must not propagate to watcher."""
        calls: list[str] = []

        async def flaky_handler(state: str) -> None:
            calls.append(state)
            if state == "bad":
                raise ValueError("handler error")

        monitor = _IterMonitor(["good", "bad", "good"])
        watcher = StateWatcher(monitor, flaky_handler)
        # Should not raise.
        await watcher.run()
        await asyncio.sleep(0)
        assert "good" in calls


# ---------------------------------------------------------------------------
# TestStop
# ---------------------------------------------------------------------------


class TestStop:
    async def test_stop_drains_normal_queue(self) -> None:
        ex = await _make_executor()
        completed: list[int] = []

        async def _work(n: int) -> None:
            completed.append(n)

        tasks = [asyncio.ensure_future(ex.submit(_work(i))) for i in range(5)]
        await asyncio.sleep(0)  # Let ensure_future tasks actually call submit()
        await ex.stop()
        assert len(completed) == 5

    async def test_stop_drains_priority_queue(self) -> None:
        ex = await _make_executor()
        completed: list[int] = []

        async def _work(n: int) -> None:
            completed.append(n)

        tasks = [
            asyncio.ensure_future(ex.submit(_work(i), priority=True)) for i in range(3)
        ]
        await asyncio.sleep(0)  # Let ensure_future tasks actually call submit()
        await ex.stop()
        assert len(completed) == 3

    async def test_stop_cancels_runner(self) -> None:
        ex = await _make_executor()
        runner = ex._runner_task
        assert runner is not None
        await ex.stop()
        assert runner.done()

    async def test_stop_allows_awaiting_submitted_futures(self) -> None:
        """Callers awaiting submitted coroutines must get their results after stop()."""
        ex = await _make_executor()

        async def _work(name: str) -> str:
            return name

        futures = [asyncio.ensure_future(ex.submit(_work(f"w{i}"))) for i in range(4)]
        await asyncio.sleep(0)  # Let ensure_future tasks call submit() first.
        await ex.stop()
        results = await asyncio.gather(*futures)
        assert results == [f"w{i}" for i in range(4)]

    async def test_stop_twice_is_safe(self) -> None:
        ex = await _make_executor()
        await ex.stop()
        await ex.stop()  # Should not raise.

    async def test_stop_before_start_is_safe(self) -> None:
        ex = SerialExecutor()
        await ex.stop()  # Should not raise.
