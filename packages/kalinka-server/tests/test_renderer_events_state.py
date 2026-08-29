"""Renderer topology on the queue event bus: the two events fold into
PlayQueueState, so the replay reports the renderers and the current one with
the queue state."""

from kalinka_eventbus.bus import EventBus
from kalinka_plugin_sdk.datamodel import PlaybackMode, PlaybackState
from kalinka_plugin_sdk.events import (
    CurrentRendererChangedEvent,
    PlayQueueEvent,
    PlayQueueEventType,
    PlayQueueState,
    RendererDescriptor,
    RenderersChangedEvent,
)


def make_state() -> PlayQueueState:
    return PlayQueueState(
        playback_state=PlaybackState(),
        track_list=[],
        playback_mode=PlaybackMode(
            shuffle=False, repeat_single=False, repeat_all=False
        ),
    )


ROW = RendererDescriptor(
    renderer_id="rid-1", friendly_name="Living Room", status="connected"
)


def test_renderers_changed_folds_into_state():
    state = make_state().apply(RenderersChangedEvent(renderers=[ROW], seq=1))

    assert state.renderers == [ROW]
    assert state.seq == 1


def test_current_renderer_changed_folds_into_state():
    state = make_state().apply(
        CurrentRendererChangedEvent(
            renderer_id="rid-1", selected_renderer_id=None, seq=1
        )
    )

    assert state.current_renderer_id == "rid-1"
    assert state.selected_renderer_id is None


def test_stale_event_is_ignored():
    state = make_state().apply(RenderersChangedEvent(renderers=[ROW], seq=2))

    assert state.apply(RenderersChangedEvent(renderers=[], seq=1)) is state


def test_wire_shape_names_the_event_types():
    renderers = RenderersChangedEvent(renderers=[ROW]).model_dump()
    current = CurrentRendererChangedEvent(renderer_id="rid-1").model_dump()

    assert renderers["event_type"] is PlayQueueEventType.RenderersChanged
    assert renderers["renderers"][0]["friendly_name"] == "Living Room"
    assert current["event_type"] is PlayQueueEventType.CurrentRendererChanged


def test_bus_snapshot_reports_renderers_with_the_queue_state():
    bus = EventBus[PlayQueueState, PlayQueueEventType, PlayQueueEvent](
        initial_state=make_state()
    )
    try:
        bus.dispatch(RenderersChangedEvent(renderers=[ROW]))
        bus.dispatch(
            CurrentRendererChangedEvent(
                renderer_id="rid-1", selected_renderer_id="rid-1"
            )
        )

        snapshot = bus.get_snapshot()
        assert snapshot.renderers == [ROW]
        assert snapshot.current_renderer_id == "rid-1"
        assert snapshot.selected_renderer_id == "rid-1"
    finally:
        bus.close()
