"""RendererRegistry lifecycle: identity by id, reconnect vs restart, reap."""

import asyncio

import pytest

from kalinka_server.renderer_registry import (
    RegistrationKind,
    RendererRegistry,
    RendererStatus,
)


def _register(registry, session, instance_id="inst-1", renderer_id="rid-1"):
    return registry.register(
        renderer_id=renderer_id,
        instance_id=instance_id,
        friendly_name="Test Renderer",
        software_version="0.1.0",
        kind="native",
        platform={"os": "linux"},
        session=session,
    )


async def test_new_registration_listed_as_connected():
    registry = RendererRegistry()
    session = object()
    assert _register(registry, session) == RegistrationKind.NEW
    (entry,) = registry.list()
    assert entry["renderer_id"] == "rid-1"
    assert entry["status"] == "connected"


async def test_unclean_disconnect_goes_offline_then_reaped():
    registry = RendererRegistry(offline_timeout_s=0.05)
    session = object()
    _register(registry, session)
    registry.disconnect("rid-1", session, clean=False)
    (entry,) = registry.list()
    assert entry["status"] == "offline"
    await asyncio.sleep(0.1)
    assert registry.list() == []


async def test_clean_goodbye_removed_immediately():
    registry = RendererRegistry()
    session = object()
    _register(registry, session)
    registry.disconnect("rid-1", session, clean=True)
    assert registry.list() == []


async def test_reconnect_same_instance_cancels_reap_and_keeps_connected_at():
    registry = RendererRegistry(offline_timeout_s=0.05)
    first = object()
    _register(registry, first)
    original_connected_at = registry.get("rid-1").connected_at
    registry.disconnect("rid-1", first, clean=False)

    second = object()
    kind = _register(registry, second)  # same instance_id
    assert kind == RegistrationKind.RECONNECT
    record = registry.get("rid-1")
    assert record.status == RendererStatus.CONNECTED
    assert record.connected_at == original_connected_at
    # The reap scheduled at disconnect must not fire after the reconnect.
    await asyncio.sleep(0.1)
    assert registry.get("rid-1") is not None


async def test_new_instance_id_is_a_restart():
    registry = RendererRegistry(offline_timeout_s=0.05)
    first = object()
    _register(registry, first, instance_id="inst-1")
    registry.disconnect("rid-1", first, clean=False)
    kind = _register(registry, object(), instance_id="inst-2")
    assert kind == RegistrationKind.RESTART
    assert registry.get("rid-1").instance_id == "inst-2"


async def test_identity_is_id_not_name():
    """A different name with the same renderer_id is the same renderer."""
    registry = RendererRegistry()
    first = object()
    _register(registry, first)
    registry.disconnect("rid-1", first, clean=False)
    kind = registry.register(
        renderer_id="rid-1",
        instance_id="inst-1",
        friendly_name="Renamed Renderer",
        software_version="0.1.0",
        kind="native",
        platform={"os": "linux"},
        session=object(),
    )
    assert kind == RegistrationKind.RECONNECT
    (entry,) = registry.list()
    assert entry["friendly_name"] == "Renamed Renderer"


async def test_live_session_replaced_and_stale_disconnect_ignored():
    replaced = []

    async def on_replace(session):
        replaced.append(session)

    registry = RendererRegistry(replace_session=on_replace)
    old = object()
    _register(registry, old)

    new = object()
    kind = _register(registry, new, instance_id="inst-2")
    assert kind == RegistrationKind.RESTART
    await asyncio.sleep(0)  # let the replace task run
    assert replaced == [old]

    # The retired session's disconnect must not clobber the new record.
    registry.disconnect("rid-1", old, clean=False)
    record = registry.get("rid-1")
    assert record.status == RendererStatus.CONNECTED
    assert record.session is new


async def test_two_renderers_are_independent():
    registry = RendererRegistry(offline_timeout_s=0.05)
    a, b = object(), object()
    _register(registry, a, renderer_id="rid-a")
    _register(registry, b, renderer_id="rid-b")
    registry.disconnect("rid-a", a, clean=False)
    await asyncio.sleep(0.1)
    ids = [e["renderer_id"] for e in registry.list()]
    assert ids == ["rid-b"]
