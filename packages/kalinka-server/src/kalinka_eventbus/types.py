from __future__ import annotations

from enum import Enum
from typing import Callable, Generic, Iterable, Optional, Protocol, TypeVar, Union

from pydantic import BaseModel, ConfigDict

E_contra = TypeVar("E_contra", bound=Enum, contravariant=True)
E = TypeVar("E", bound=Enum)
S = TypeVar("S")


class BaseEvent(BaseModel, Generic[E]):
    """Base event type for user-defined events.

    Users should subclass this and add event-specific fields.
    `event_type` should be set to an enum value describing the event.
    Global sequence `seq` is assigned by the bus on dispatch.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    event_type: E
    seq: int = 0


class ReplayEvent(BaseModel, Generic[S]):
    """Synthetic event delivered first on subscription containing a snapshot of state.

    `seq` is the per-subscription sequence number (starts at 0).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    state: S
    seq: int


EV_emit = TypeVar("EV_emit", bound=BaseEvent, contravariant=True)
EV_listen = TypeVar("EV_listen", bound=BaseEvent, covariant=True)


class EventEmitter(Protocol[EV_emit]):
    def dispatch(self, event: EV_emit) -> None:
        """Dispatch a new event to the bus (thread-safe)."""
        ...


class EventListener(Protocol[E_contra, EV_listen, S]):
    def subscribe(
        self,
        event_types: Iterable[E_contra],
        callback: Optional[Callable[[Union[EV_listen, ReplayEvent[S]]], None]] = None,
        listener_id: Optional[str] = None,
    ) -> "Subscription":
        """Subscribe to event types with optional callback; returns a Subscription."""
        ...

    def unsubscribe(self, listener_id: str) -> None:
        """Unsubscribe and close the listener's queue."""
        ...


class Subscription(Protocol):
    @property
    def id(self) -> str:  # unique per subscription
        ...

    def unsubscribe(self) -> None: ...
