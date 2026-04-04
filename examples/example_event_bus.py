"""
Example: Using the EventBus with asyncio interface and multiple subscribers.

This example demonstrates:
- Defining a custom state and event types
- Creating an EventBus with initial state
- Publishing events from a coroutine
- Multiple async subscribers listening to events
"""

import asyncio
from enum import Enum
from typing import Optional

from pydantic import BaseModel

from packages.kalinka_server.src.kalinka_eventbus.bus import EventBus
from packages.kalinka_server.src.kalinka_eventbus.types import BaseEvent, ReplayEvent


# ============================================================================
# Define State and Events
# ============================================================================


class Counter(BaseModel):
    """Application state: a simple counter."""

    value: int = 0

    def apply(self, event: "CounterEvent") -> None:
        """Apply an event to update state."""
        if isinstance(event, CounterIncremented):
            self.value += event.amount
        elif isinstance(event, CounterReset):
            self.value = event.initial_value


class CounterEventType(Enum):
    """Enumeration of event types."""

    INCREMENTED = "incremented"
    RESET = "reset"


class CounterEvent(BaseEvent[CounterEventType]):
    """Base class for counter events."""

    pass


class CounterIncremented(CounterEvent):
    """Event: counter was incremented."""

    event_type: CounterEventType = CounterEventType.INCREMENTED
    amount: int


class CounterReset(CounterEvent):
    """Event: counter was reset."""

    event_type: CounterEventType = CounterEventType.RESET
    initial_value: int


# ============================================================================
# Subscriber 1: Logs all events
# ============================================================================


async def subscriber_logger(
    bus: EventBus[Counter, CounterEventType, CounterEvent],
) -> None:
    """Subscriber 1: Logs all events to console."""
    print("📝 Logger subscriber started, listening for all counter events...")

    async with bus.aiter(
        [CounterEventType.INCREMENTED, CounterEventType.RESET]
    ) as stream:
        async for item in stream:
            if isinstance(item, ReplayEvent):
                print(f"  [REPLAY] Current state: counter = {item.state.value}")
            else:
                print(
                    f"  [EVENT] {item.event_type.value}: {item.__dict__} "
                    f"(global_seq={item.seq})"
                )


# ============================================================================
# Subscriber 2: Tracks counter milestones
# ============================================================================


async def subscriber_milestone_tracker(
    bus: EventBus[Counter, CounterEventType, CounterEvent],
) -> None:
    """Subscriber 2: Tracks when counter reaches milestones (10, 20, 30, etc.)."""
    print("🎯 Milestone tracker subscriber started...")

    milestones_reached = set()

    async with bus.aiter(
        [CounterEventType.INCREMENTED, CounterEventType.RESET]
    ) as stream:
        async for item in stream:
            if isinstance(item, ReplayEvent):
                current = item.state.value
            else:
                current = item.state.value if hasattr(item, "state") else None

            # Check if we've reached a new milestone
            if current is not None:
                milestone = (current // 10) * 10
                if milestone > 0 and milestone not in milestones_reached:
                    milestones_reached.add(milestone)
                    print(f"  🏁 Milestone reached: counter = {current}")


# ============================================================================
# Publisher: Emits events
# ============================================================================


async def publisher(
    bus: EventBus[Counter, CounterEventType, CounterEvent],
) -> None:
    """Publishes events to the bus."""
    print("📤 Publisher started, will emit events every 0.5 seconds...")

    for i in range(1, 6):
        await asyncio.sleep(0.5)
        event = CounterIncremented(amount=5)
        print(f"\n→ Publishing: CounterIncremented(amount=5)")
        bus.dispatch(event)

    await asyncio.sleep(0.5)
    print(f"\n→ Publishing: CounterReset(initial_value=0)")
    bus.dispatch(CounterReset(initial_value=0))

    await asyncio.sleep(0.5)
    print(f"\n→ Publishing: CounterIncremented(amount=100)")
    bus.dispatch(CounterIncremented(amount=100))

    print("\n✅ Publisher finished emitting events")


# ============================================================================
# Main: Orchestrate everything
# ============================================================================


async def main() -> None:
    """Main coroutine: create bus and run subscribers + publisher concurrently."""
    # Create the event bus with initial state
    initial_state = Counter(value=0)
    bus: EventBus[Counter, CounterEventType, CounterEvent] = EventBus(
        initial_state,
        max_queue_size=1000,
        low_watermark=500,
    )

    print("=" * 70)
    print("EventBus Example: Multiple Async Subscribers")
    print("=" * 70)
    print()

    try:
        # Run publisher and two subscribers concurrently
        # The publisher emits events while subscribers listen
        tasks = [
            asyncio.create_task(publisher(bus)),
            asyncio.create_task(subscriber_logger(bus)),
            asyncio.create_task(subscriber_milestone_tracker(bus)),
        ]

        # Wait for publisher to finish, then cancel subscribers after a delay
        await tasks[0]  # Wait for publisher
        await asyncio.sleep(1)  # Give time for events to propagate

        # Cancel subscriber tasks
        for task in tasks[1:]:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        print("\n" + "=" * 70)
        print(f"Final state: counter = {bus._state.value}")
        print("=" * 70)

    finally:
        bus.close()
        print("\n✨ Event bus closed")


if __name__ == "__main__":
    asyncio.run(main())
