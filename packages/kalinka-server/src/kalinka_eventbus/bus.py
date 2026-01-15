from __future__ import annotations

import asyncio
import copy
import threading
import uuid
from collections import deque
from enum import Enum
from typing import (
    Callable,
    Deque,
    Dict,
    Generic,
    Iterable,
    List,
    Optional,
    Set,
    TypeVar,
    Union,
)

from kalinka_plugin_sdk.api import (
    BaseEvent,
    ReplayEvent,
    EventEmitter,
    EventListener,
    Subscription,
)

E = TypeVar("E", bound=Enum)
S = TypeVar("S")
EV = TypeVar("EV", bound=BaseEvent)

# Async stream type vars (distinct to avoid shadowing warnings)
E_stream = TypeVar("E_stream", bound=Enum)
S_stream = TypeVar("S_stream")
EV_stream = TypeVar("EV_stream", bound=BaseEvent)


class _Subscription(Generic[S, E, EV]):
    def __init__(
        self,
        listener_id: str,
        event_types: Iterable[E],
        since_global_seq: int,
        max_queue_size: int,
        low_watermark: int,
    ) -> None:
        self._id = listener_id
        self.event_types: Set[E] = set(event_types)
        self.since_seq: int = since_global_seq
        self._max_size = max_queue_size
        self._low_watermark = low_watermark

        self._q: Deque[Union[ReplayEvent[S], EV]] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._closed = False

        self._callbacks: List[Callable[[Union[EV, ReplayEvent[S]]], None]] = []
        self._workers: List[threading.Thread] = []

        # Per-subscription sequence numbering (renumbered starting at 0)
        self._next_sub_seq = 0

        # Local state snapshot for coalescing
        self._local_state: Optional[S] = None

    @property
    def id(self) -> str:
        return self._id

    def unsubscribe(self) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._not_empty.notify_all()

    def add_callback(self, cb: Callable[[Union[EV, ReplayEvent[S]]], None]) -> None:
        self._callbacks.append(cb)
        t = threading.Thread(
            target=self._worker_loop, name=f"sub-worker-{self._id}", daemon=True
        )
        self._workers.append(t)
        t.start()

    def _worker_loop(self) -> None:
        while True:
            item: Optional[Union[ReplayEvent[S], EV]] = None
            with self._lock:
                while not self._q and not self._closed:
                    self._not_empty.wait()
                if self._closed:
                    return
                item = self._q.popleft()
            # Deliver outside lock
            for cb in self._callbacks:
                try:
                    cb(item)  # type: ignore[arg-type]
                except Exception:
                    # Swallow to keep other callbacks running
                    pass

    def enqueue_replay(self, state: S) -> None:
        with self._lock:
            if self._closed:
                return
            # Initialize local state and first replay event
            self._local_state = copy.deepcopy(state)
            replay = ReplayEvent[S](
                state=copy.deepcopy(self._local_state), seq=self._next_sub_seq
            )
            self._next_sub_seq += 1
            self._q.append(replay)
            self._not_empty.notify()

    def enqueue_event(self, event: EV) -> None:
        with self._lock:
            if self._closed:
                return
            # Copy event with renumbered per-subscription sequence
            ev_copy = event.model_copy(update={"seq": self._next_sub_seq})
            self._next_sub_seq += 1
            self._q.append(ev_copy)  # type: ignore[arg-type]

            # Coalesce if beyond capacity
            if len(self._q) > self._max_size:
                self._coalesce_locked()

            self._not_empty.notify()

    def _coalesce_locked(self) -> None:
        # Pop from left until size reaches low watermark, folding into local state
        if self._local_state is None:
            # If somehow not set yet, try to set from first replay if present
            for itm in self._q:
                if isinstance(itm, ReplayEvent):
                    self._local_state = copy.deepcopy(itm.state)
                    break
            if self._local_state is None:
                # Nothing to coalesce against yet
                return

        removed_any = False
        first_seq: Optional[int] = None
        while len(self._q) > self._low_watermark:
            itm = self._q.popleft()
            if first_seq is None:
                # Determine starting seq of collapsed window
                if isinstance(itm, ReplayEvent):
                    first_seq = itm.seq
                else:
                    first_seq = getattr(itm, "seq", 0)
            removed_any = True

            # Update local state snapshot
            if isinstance(itm, ReplayEvent):
                self._local_state = copy.deepcopy(itm.state)
            else:
                # Apply event to local state
                try:
                    # type: ignore[attr-defined]
                    self._local_state.apply(itm)  # type: ignore[arg-type]
                except Exception:
                    # If state apply fails, skip applying but continue coalescing
                    pass

        if removed_any and self._local_state is not None and first_seq is not None:
            # Insert a new replay event representing the collapsed prefix
            collapsed = ReplayEvent[S](
                state=copy.deepcopy(self._local_state), seq=first_seq
            )
            self._q.appendleft(collapsed)


# Async iteration helper
class AsyncEventStream(Generic[S_stream, E_stream, EV_stream]):
    def __init__(
        self,
        bus: "EventBus[S_stream, E_stream, EV_stream]",
        event_types: Iterable[E_stream],
    ):
        self._bus = bus
        self._event_types = list(event_types)
        self._queue: Optional[
            asyncio.Queue[Union[EV_stream, ReplayEvent[S_stream]]]
        ] = None
        self._sub_id: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stopped = False

    async def __aenter__(
        self,
    ) -> "AsyncEventStream[S_stream, E_stream, EV_stream]":
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()

        def _cb(item: Union[EV_stream, ReplayEvent[S_stream]]) -> None:
            # Bridge from worker thread into async loop queue
            if self._queue is None or self._loop is None:
                return
            self._loop.call_soon_threadsafe(self._queue.put_nowait, item)

        sub = self._bus.subscribe(self._event_types, callback=_cb)
        self._sub_id = sub.id
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._sub_id is not None:
            self._bus.unsubscribe(self._sub_id)
            self._sub_id = None
        self._stopped = True
        # Drain queue to unblock any waiters
        if self._queue is not None:
            self._queue.put_nowait(StopAsyncIteration)  # type: ignore[arg-type]

    def __aiter__(self):
        return self

    async def __anext__(self) -> Union[EV_stream, ReplayEvent[S_stream]]:
        if self._queue is None:
            raise StopAsyncIteration
        item = await self._queue.get()
        if item is StopAsyncIteration:
            raise StopAsyncIteration
        return item  # type: ignore[return-value]


class EventBus(Generic[S, E, EV], EventEmitter[EV], EventListener[E, EV, S]):
    def __init__(
        self,
        initial_state: S,
        *,
        max_queue_size: int = 1000,
        low_watermark: int = 500,
    ) -> None:
        if low_watermark >= max_queue_size:
            raise ValueError("low_watermark must be < max_queue_size")
        self._state: S = initial_state
        self._state_lock = threading.RLock()
        self._next_global_seq: int = 0

        # Registry of subscriptions
        self._subs_lock = threading.RLock()
        self._subs: Dict[str, _Subscription[S, E, EV]] = {}

        # Fan-out infrastructure
        self._inbound: Deque[EV] = deque()
        self._inbound_lock = threading.Lock()
        self._inbound_not_empty = threading.Condition(self._inbound_lock)
        self._stop = False
        self._fanout_thread = threading.Thread(
            target=self._fanout_loop, name="eventbus-fanout", daemon=True
        )

        self._max_queue_size = max_queue_size
        self._low_watermark = low_watermark

        self._fanout_thread.start()

    # Emitter API
    def dispatch(self, event: EV) -> None:
        # Assign global seq and apply to state atomically
        with self._state_lock:
            seq = self._next_global_seq
            self._next_global_seq += 1
            ev_with_seq: EV = event.model_copy(update={"seq": seq})  # type: ignore[assignment]
            # User state must implement apply(event)
            try:
                # type: ignore[attr-defined]
                self._state.apply(ev_with_seq)  # type: ignore[arg-type]
            except Exception:
                # Do not block dispatch if apply fails; still deliver event
                pass

        # Enqueue for fan-out without holding the state lock
        with self._inbound_lock:
            self._inbound.append(ev_with_seq)
            self._inbound_not_empty.notify()

    # Listener API
    def subscribe(
        self,
        event_types: Iterable[E],
        callback: Optional[Callable[[Union[EV, ReplayEvent[S]]], None]] = None,
        listener_id: Optional[str] = None,
    ) -> Subscription:
        # Reuse existing subscription if listener_id provided and exists
        if listener_id is not None:
            with self._subs_lock:
                existing = self._subs.get(listener_id)
                if existing is not None:
                    if callback is not None:
                        existing.add_callback(callback)
                    return existing

        # Create new subscription and register atomically with a consistent snapshot seq
        with self._state_lock:
            last_seq = self._next_global_seq - 1
            # Snapshot current state for replay
            snapshot_state = copy.deepcopy(self._state)

            sub_id = listener_id or str(uuid.uuid4())
            sub = _Subscription[S, E, EV](
                listener_id=sub_id,
                event_types=event_types,
                since_global_seq=last_seq,
                max_queue_size=self._max_queue_size,
                low_watermark=self._low_watermark,
            )
            with self._subs_lock:
                self._subs[sub_id] = sub
            # Enqueue replay while still holding state lock to guarantee ordering
            sub.enqueue_replay(snapshot_state)
        if callback is not None:
            sub.add_callback(callback)
        return sub

    def unsubscribe(self, listener_id: str) -> None:
        with self._subs_lock:
            sub = self._subs.pop(listener_id, None)
        if sub is not None:
            sub.close()

    def get_snapshot(self) -> S:
        """Get a deep copy snapshot of the current EventBus state.

        Returns:
            A deep copy of the current state, safe to use without affecting the EventBus.
        """
        with self._state_lock:
            return copy.deepcopy(self._state)

    def stream(self, event_types: Iterable[E]) -> "AsyncEventStream[S, E, EV]":
        return AsyncEventStream(self, event_types)

    # Internal fan-out loop
    def _fanout_loop(self) -> None:
        while not self._stop:
            ev: Optional[EV] = None
            with self._inbound_lock:
                while not self._inbound and not self._stop:
                    self._inbound_not_empty.wait()
                if self._stop:
                    return
                ev = self._inbound.popleft()

            if ev is None:
                continue

            # Snapshot subs to iterate without holding lock long
            with self._subs_lock:
                subs = list(self._subs.values())

            for sub in subs:
                try:
                    # Only deliver events emitted strictly after subscription snapshot
                    if getattr(ev, "seq", -1) <= sub.since_seq:
                        continue
                    # Filter by event type
                    if ev.event_type not in sub.event_types:  # type: ignore[attr-defined]
                        continue
                    sub.enqueue_event(ev)
                except Exception:
                    # Ignore failures for individual subscribers
                    pass

    def close(self) -> None:
        self._stop = True
        with self._inbound_lock:
            self._inbound_not_empty.notify_all()
        self._fanout_thread.join(timeout=1.0)
        # Close all subs
        with self._subs_lock:
            subs = list(self._subs.values())
            self._subs.clear()
        for sub in subs:
            sub.close()
