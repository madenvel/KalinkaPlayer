"""Tests for kalinka_eventbus module."""

from __future__ import annotations

import asyncio
import copy
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Union

import pytest

from kalinka_plugin_sdk.api import BaseEvent, ReplayEvent
from kalinka_eventbus.bus import EventBus


# Test fixtures and types
class TestEventType(Enum):
    """Event types for testing."""

    EVENT_A = "event_a"
    EVENT_B = "event_b"
    EVENT_C = "event_c"


@dataclass
class TestState:
    """Simple state for testing."""

    counter: int = 0
    values: List[str] = field(default_factory=list)

    def apply(self, event: "TestEvent") -> None:
        """Apply an event to update state."""
        if event.event_type == TestEventType.EVENT_A:
            self.counter += event.increment
        elif event.event_type == TestEventType.EVENT_B:
            self.values.append(event.value)
        elif event.event_type == TestEventType.EVENT_C:
            self.counter -= 1


class TestEvent(BaseEvent[TestEventType]):
    """Test event with optional fields."""

    increment: int = 0
    value: str = ""


# Tests
class TestEventBusBasics:
    """Test basic EventBus functionality."""

    def test_eventbus_creation(self):
        """Test EventBus can be created with initial state."""
        initial_state = TestState(counter=5)
        bus = EventBus[TestState, TestEventType, TestEvent](initial_state)
        assert bus is not None
        bus.close()

    def test_eventbus_invalid_watermarks(self):
        """Test EventBus rejects invalid watermark configuration."""
        initial_state = TestState()
        with pytest.raises(ValueError, match="low_watermark must be < max_queue_size"):
            EventBus[TestState, TestEventType, TestEvent](
                initial_state, max_queue_size=100, low_watermark=100
            )

    def test_dispatch_assigns_sequence(self):
        """Test that dispatch assigns sequential per-subscription sequence numbers."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())
        received_events = []

        def callback(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            if isinstance(event, TestEvent):
                received_events.append(event)

        bus.subscribe([TestEventType.EVENT_A], callback=callback)

        # Give subscription time to receive replay
        time.sleep(0.05)

        # Dispatch events
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=1))
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=2))
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=3))

        # Wait for events to be delivered
        time.sleep(0.1)

        bus.close()

        # Per-subscription sequences start at 1 (0 is for ReplayEvent)
        assert len(received_events) == 3
        assert received_events[0].seq == 1
        assert received_events[1].seq == 2
        assert received_events[2].seq == 3


class TestEventBusSubscription:
    """Test subscription functionality."""

    def test_subscribe_receives_replay(self):
        """Test that new subscriptions receive a replay event with current state."""
        initial_state = TestState(counter=42, values=["hello"])
        bus = EventBus[TestState, TestEventType, TestEvent](initial_state)

        received = []

        def callback(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            received.append(event)

        bus.subscribe([TestEventType.EVENT_A], callback=callback)

        # Wait for replay to be delivered
        time.sleep(0.1)

        bus.close()

        assert len(received) >= 1
        assert isinstance(received[0], ReplayEvent)
        assert received[0].state.counter == 42
        assert received[0].state.values == ["hello"]
        assert received[0].seq == 0

    def test_subscribe_with_listener_id(self):
        """Test subscription with custom listener ID."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())

        sub = bus.subscribe([TestEventType.EVENT_A], listener_id="custom-id")

        assert sub.id == "custom-id"

        bus.close()

    def test_subscribe_reuses_existing_listener_id(self):
        """Test that subscribing with existing listener_id reuses the subscription."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())

        received1 = []
        received2 = []

        def callback1(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            received1.append(event)

        def callback2(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            received2.append(event)

        sub1 = bus.subscribe(
            [TestEventType.EVENT_A], callback=callback1, listener_id="shared-id"
        )
        sub2 = bus.subscribe(
            [TestEventType.EVENT_A], callback=callback2, listener_id="shared-id"
        )

        assert sub1.id == sub2.id == "shared-id"

        # Dispatch event
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=1))

        time.sleep(0.1)
        bus.close()

        # Both callbacks should receive the event
        assert len(received1) >= 1
        assert len(received2) >= 1

    def test_subscribe_filters_by_event_type(self):
        """Test that subscriptions only receive specified event types."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())

        received_a = []
        received_b = []

        def callback_a(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            received_a.append(event)

        def callback_b(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            received_b.append(event)

        bus.subscribe([TestEventType.EVENT_A], callback=callback_a)
        bus.subscribe([TestEventType.EVENT_B], callback=callback_b)

        time.sleep(0.05)

        # Dispatch different event types
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=1))
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_B, value="test"))
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=2))

        time.sleep(0.1)
        bus.close()

        # Filter out replay events and check
        events_a = [e for e in received_a if isinstance(e, TestEvent)]
        events_b = [e for e in received_b if isinstance(e, TestEvent)]

        assert len(events_a) == 2
        assert all(e.event_type == TestEventType.EVENT_A for e in events_a)

        assert len(events_b) == 1
        assert all(e.event_type == TestEventType.EVENT_B for e in events_b)

    def test_unsubscribe(self):
        """Test that unsubscribe stops event delivery."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())

        received = []

        def callback(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            received.append(event)

        sub = bus.subscribe([TestEventType.EVENT_A], callback=callback)

        time.sleep(0.05)

        # Dispatch event before unsubscribe
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=1))
        time.sleep(0.05)

        count_before_unsub = len([e for e in received if isinstance(e, TestEvent)])

        # Unsubscribe
        bus.unsubscribe(sub.id)

        # Dispatch event after unsubscribe
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=2))
        time.sleep(0.05)

        count_after_unsub = len([e for e in received if isinstance(e, TestEvent)])

        bus.close()

        # Should have received first event but not second
        assert count_before_unsub == 1
        assert count_after_unsub == 1

    def test_subscription_unsubscribe_method(self):
        """Test that subscription.unsubscribe() works."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())

        received = []

        def callback(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            received.append(event)

        sub = bus.subscribe([TestEventType.EVENT_A], callback=callback)

        time.sleep(0.05)

        # Dispatch event before unsubscribe
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=1))
        time.sleep(0.05)

        count_before_unsub = len([e for e in received if isinstance(e, TestEvent)])

        # Unsubscribe via subscription method
        sub.unsubscribe()

        # Dispatch event after unsubscribe
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=2))
        time.sleep(0.05)

        count_after_unsub = len([e for e in received if isinstance(e, TestEvent)])

        bus.close()

        assert count_before_unsub == 1
        assert count_after_unsub == 1


class TestStateApplication:
    """Test state application and replay."""

    def test_state_is_updated_on_dispatch(self):
        """Test that state is updated when events are dispatched."""
        initial_state = TestState(counter=0)
        bus = EventBus[TestState, TestEventType, TestEvent](initial_state)

        # Subscribe to capture state snapshots
        snapshots = []

        def callback(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            if isinstance(event, ReplayEvent):
                snapshots.append(copy.deepcopy(event.state))

        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=5))
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_B, value="test"))

        # Wait for events to be applied
        time.sleep(0.05)

        # Subscribe to get current state
        bus.subscribe([TestEventType.EVENT_A], callback=callback)
        time.sleep(0.05)

        bus.close()

        # Replay should contain updated state
        assert len(snapshots) >= 1
        assert snapshots[0].counter == 5
        assert "test" in snapshots[0].values

    def test_replay_renumbers_sequences(self):
        """Test that per-subscription sequences start from 0."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())

        # Dispatch some events to advance global sequence
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=1))
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=2))

        time.sleep(0.05)

        received = []

        def callback(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            received.append(event)

        # Subscribe after global sequence has advanced
        bus.subscribe([TestEventType.EVENT_A], callback=callback)

        # Dispatch more events
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=3))

        time.sleep(0.1)
        bus.close()

        # First event should be replay with seq=0
        assert len(received) >= 1
        assert isinstance(received[0], ReplayEvent)
        assert received[0].seq == 0

        # Subsequent events should have sequential per-sub sequences
        if len(received) > 1:
            assert received[1].seq == 1


class TestCoalescing:
    """Test queue coalescing behavior."""

    def test_coalescing_on_queue_overflow(self):
        """Test that queue coalescing occurs when max size is exceeded."""
        bus = EventBus[TestState, TestEventType, TestEvent](
            TestState(), max_queue_size=10, low_watermark=5
        )

        received = []
        received_lock = threading.Lock()

        def slow_callback(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            # Simulate slow consumer to force queue buildup
            time.sleep(0.02)
            with received_lock:
                received.append(event)

        bus.subscribe([TestEventType.EVENT_A], callback=slow_callback)

        time.sleep(0.05)

        # Dispatch many events quickly to overflow queue
        for i in range(20):
            bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=1))

        # Wait for processing
        time.sleep(1.0)
        bus.close()

        with received_lock:
            # Should have received some events, but coalescing may have occurred
            # At minimum, we should have received something
            assert len(received) > 0

            # Should contain at least one ReplayEvent (initial or coalesced)
            replay_events = [e for e in received if isinstance(e, ReplayEvent)]
            assert len(replay_events) >= 1


class TestAsyncIteration:
    """Test async iteration functionality."""

    @pytest.mark.asyncio
    async def test_async_iteration_basic(self):
        """Test basic async iteration over events."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())

        received = []

        async def consumer():
            async with bus.stream(
                [TestEventType.EVENT_A, TestEventType.EVENT_B]
            ) as stream:
                async for event in stream:
                    received.append(event)
                    if isinstance(event, TestEvent) and event.increment == 3:
                        break

        # Start consumer
        consumer_task = asyncio.create_task(consumer())

        # Give it time to subscribe
        await asyncio.sleep(0.1)

        # Dispatch events
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=1))
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_B, value="test"))
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=3))

        # Wait for consumer
        await asyncio.wait_for(consumer_task, timeout=2.0)

        bus.close()

        # Should have received replay + events
        assert len(received) >= 3
        assert isinstance(received[0], ReplayEvent)

        test_events = [e for e in received if isinstance(e, TestEvent)]
        assert len(test_events) >= 2

    @pytest.mark.asyncio
    async def test_async_iteration_filters_types(self):
        """Test that async iteration filters by event type."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())

        received = []

        async def consumer():
            async with bus.stream([TestEventType.EVENT_A]) as stream:
                count = 0
                async for event in stream:
                    received.append(event)
                    if isinstance(event, TestEvent):
                        count += 1
                        if count >= 2:
                            break

        consumer_task = asyncio.create_task(consumer())
        await asyncio.sleep(0.1)

        # Dispatch mixed event types
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=1))
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_B, value="skip"))
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=2))
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_C))

        await asyncio.wait_for(consumer_task, timeout=2.0)
        bus.close()

        # Filter to TestEvent only
        test_events = [e for e in received if isinstance(e, TestEvent)]

        # Should only have EVENT_A types
        assert all(e.event_type == TestEventType.EVENT_A for e in test_events)
        assert len(test_events) == 2


class TestConcurrency:
    """Test thread-safety and concurrent operations."""

    def test_concurrent_dispatch(self):
        """Test that concurrent dispatches are handled correctly."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())

        num_threads = 5
        events_per_thread = 20
        received = []
        lock = threading.Lock()

        def callback(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            with lock:
                received.append(event)

        bus.subscribe([TestEventType.EVENT_A], callback=callback)
        time.sleep(0.05)

        def dispatch_events(thread_id: int):
            for i in range(events_per_thread):
                bus.dispatch(
                    TestEvent(event_type=TestEventType.EVENT_A, increment=thread_id)
                )

        threads = [
            threading.Thread(target=dispatch_events, args=(i,))
            for i in range(num_threads)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        time.sleep(0.5)
        bus.close()

        # Should have received all events plus one replay
        test_events = [e for e in received if isinstance(e, TestEvent)]
        assert len(test_events) == num_threads * events_per_thread

        # Sequences should be unique and sequential
        sequences = [e.seq for e in test_events]
        assert len(set(sequences)) == len(sequences)  # All unique

    def test_concurrent_subscribe_unsubscribe(self):
        """Test concurrent subscription and unsubscription."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())

        def subscribe_unsubscribe_loop():
            for _ in range(10):
                sub = bus.subscribe([TestEventType.EVENT_A])
                time.sleep(0.001)
                bus.unsubscribe(sub.id)

        threads = [
            threading.Thread(target=subscribe_unsubscribe_loop) for _ in range(3)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        bus.close()
        # Test passes if no exceptions/deadlocks occur


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_dispatch_after_close(self):
        """Test that dispatch after close doesn't crash."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())
        bus.close()

        # Should not crash
        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=1))

    def test_subscribe_after_close(self):
        """Test that subscription after close works but doesn't receive events."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())
        bus.close()

        received = []

        def callback(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            received.append(event)

        # Subscribe after close
        sub = bus.subscribe([TestEventType.EVENT_A], callback=callback)

        time.sleep(0.1)

        # May receive replay but no new events
        assert sub is not None

    def test_multiple_callbacks_on_subscription(self):
        """Test adding multiple callbacks to same subscription."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())

        received1 = []
        received2 = []

        def callback1(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            received1.append(event)

        def callback2(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            received2.append(event)

        sub = bus.subscribe(
            [TestEventType.EVENT_A], callback=callback1, listener_id="multi"
        )
        bus.subscribe([TestEventType.EVENT_A], callback=callback2, listener_id="multi")

        time.sleep(0.05)

        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=1))

        time.sleep(0.1)
        bus.close()

        # Both callbacks should receive events
        assert len(received1) >= 1
        assert len(received2) >= 1

    def test_callback_exception_does_not_break_delivery(self):
        """Test that exception in one callback doesn't affect others."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())

        received_good = []

        def bad_callback(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            raise RuntimeError("Intentional error")

        def good_callback(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            received_good.append(event)

        bus.subscribe([TestEventType.EVENT_A], callback=bad_callback)
        bus.subscribe([TestEventType.EVENT_A], callback=good_callback)

        time.sleep(0.05)

        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=1))

        time.sleep(0.1)
        bus.close()

        # Good callback should still receive event despite bad callback failing
        test_events = [e for e in received_good if isinstance(e, TestEvent)]
        assert len(test_events) >= 1

    def test_empty_event_types_subscription(self):
        """Test subscription with empty event types list."""
        bus = EventBus[TestState, TestEventType, TestEvent](TestState())

        received = []

        def callback(event: Union[TestEvent, ReplayEvent[TestState]]) -> None:
            received.append(event)

        sub = bus.subscribe([], callback=callback)

        time.sleep(0.05)

        bus.dispatch(TestEvent(event_type=TestEventType.EVENT_A, increment=1))

        time.sleep(0.1)
        bus.close()

        # Should only receive replay, no events
        test_events = [e for e in received if isinstance(e, TestEvent)]
        assert len(test_events) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
