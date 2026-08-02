import asyncio

import pytest

from kalinka_server.renderer_registry import RendererRegistry
from kalinka_server.renderer_sessions import (
    CloseReason,
    RendererBusy,
    RendererUnavailable,
    SessionOpenFailed,
    SessionPool,
    SessionState,
)

SERVER_ID = "11111111-1111-4111-8111-111111111111"
OTHER_SERVER_ID = "22222222-2222-4222-8222-222222222222"
RENDERER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class FakeWs:
    """Renderer end of the wire: records what the pool sends, replies on cue."""

    def __init__(self, pool=None, accept=True, busy_owner=""):
        self.pool = pool
        self.accept = accept
        self.busy_owner = busy_owner
        self.opened: list[str] = []
        self.closed: list[tuple[str, CloseReason]] = []
        self.answer = True

    async def send_session_open(self, session_id):
        self.opened.append(session_id)
        if self.answer:
            self.pool.handle_open_result(
                RENDERER_ID,
                session_id=session_id,
                accepted=self.accept,
                busy=not self.accept,
                detail="" if self.accept else "busy",
                owner_server_id=self.busy_owner,
            )

    async def send_session_close(self, session_id, reason):
        self.closed.append((session_id, reason))

    async def replace(self):
        pass


def make_pool(open_timeout_s=5.0):
    sessions: list = []
    registry = RendererRegistry(
        offline_timeout_s=30.0,
        on_removed=lambda rid: pool.handle_renderer_removed(rid),
    )
    pool = SessionPool(registry, SERVER_ID, open_timeout_s=open_timeout_s)
    return registry, pool


def register(registry, ws, instance_id="inst-1"):
    registry.register(
        renderer_id=RENDERER_ID,
        instance_id=instance_id,
        friendly_name="Test Renderer",
        software_version="0.1.0",
        kind="native",
        platform={},
        session=ws,
    )


@pytest.mark.asyncio
async def test_open_and_close():
    registry, pool = make_pool()
    ws = FakeWs(pool)
    register(registry, ws)

    session = await pool.open(RENDERER_ID)
    assert session.state is SessionState.ACTIVE
    assert ws.opened == [session.session_id]
    assert pool.get(RENDERER_ID) is session

    closed: list = []
    session.on_closed(lambda s, reason: closed.append(reason))
    await session.close()

    assert closed == [CloseReason.CLOSED_BY_SERVER]
    assert session.state is SessionState.CLOSED
    assert pool.get(RENDERER_ID) is None
    assert ws.closed == [(session.session_id, CloseReason.CLOSED_BY_SERVER)]

    await session.close()  # idempotent
    assert closed == [CloseReason.CLOSED_BY_SERVER]


@pytest.mark.asyncio
async def test_open_requires_a_connected_renderer():
    registry, pool = make_pool()
    with pytest.raises(RendererUnavailable):
        await pool.open(RENDERER_ID)


@pytest.mark.asyncio
async def test_second_open_is_busy():
    registry, pool = make_pool()
    ws = FakeWs(pool)
    register(registry, ws)

    await pool.open(RENDERER_ID)
    with pytest.raises(RendererBusy) as excinfo:
        await pool.open(RENDERER_ID)
    assert excinfo.value.owner_server_id == SERVER_ID


@pytest.mark.asyncio
async def test_renderer_refuses_as_busy():
    registry, pool = make_pool()
    ws = FakeWs(pool, accept=False, busy_owner=OTHER_SERVER_ID)
    register(registry, ws)

    with pytest.raises(RendererBusy) as excinfo:
        await pool.open(RENDERER_ID)
    assert excinfo.value.owner_server_id == OTHER_SERVER_ID
    assert pool.get(RENDERER_ID) is None


@pytest.mark.asyncio
async def test_open_times_out_and_releases_the_claim():
    registry, pool = make_pool(open_timeout_s=0.05)
    ws = FakeWs(pool)
    ws.answer = False
    register(registry, ws)

    with pytest.raises(asyncio.TimeoutError):
        await pool.open(RENDERER_ID)
    assert pool.get(RENDERER_ID) is None
    assert ws.closed and ws.closed[0][1] == CloseReason.CLOSED_BY_SERVER


@pytest.mark.asyncio
async def test_reconnect_resumes_the_session():
    registry, pool = make_pool()
    ws = FakeWs(pool)
    register(registry, ws)
    session = await pool.open(RENDERER_ID)

    registry.disconnect(RENDERER_ID, ws, clean=False)
    pool.suspend(RENDERER_ID, ws)
    assert session.state is SessionState.SUSPENDED
    assert not session.connected

    ws2 = FakeWs(pool)
    register(registry, ws2)
    await pool.reconcile(
        renderer_id=RENDERER_ID,
        reported_session_id=session.session_id,
        reported_owner_server_id=SERVER_ID,
        ws_session=ws2,
    )

    assert session.state is SessionState.ACTIVE
    assert session.connected
    assert pool.get(RENDERER_ID) is session
    assert ws2.closed == []


@pytest.mark.asyncio
async def test_renderer_restart_closes_the_session():
    registry, pool = make_pool()
    ws = FakeWs(pool)
    register(registry, ws)
    session = await pool.open(RENDERER_ID)
    closed: list = []
    session.on_closed(lambda s, reason: closed.append(reason))

    registry.disconnect(RENDERER_ID, ws, clean=False)
    pool.suspend(RENDERER_ID, ws)

    ws2 = FakeWs(pool)
    register(registry, ws2, instance_id="inst-2")
    await pool.reconcile(
        renderer_id=RENDERER_ID,
        reported_session_id="",
        reported_owner_server_id="",
        ws_session=ws2,
    )

    assert closed == [CloseReason.RENDERER_RESTARTED]
    assert pool.get(RENDERER_ID) is None
    assert ws2.closed == []  # nothing to tell the renderer about


@pytest.mark.asyncio
async def test_orphaned_session_is_closed_as_stale():
    registry, pool = make_pool()
    ws = FakeWs(pool)
    register(registry, ws)

    # This Core has no memory of the session (it restarted), but the renderer
    # is still running one it opened.
    await pool.reconcile(
        renderer_id=RENDERER_ID,
        reported_session_id="orphan-session",
        reported_owner_server_id=SERVER_ID,
        ws_session=ws,
    )

    assert ws.closed == [("orphan-session", CloseReason.STALE)]


@pytest.mark.asyncio
async def test_another_cores_session_is_left_alone():
    registry, pool = make_pool()
    ws = FakeWs(pool)
    register(registry, ws)

    await pool.reconcile(
        renderer_id=RENDERER_ID,
        reported_session_id="not-ours",
        reported_owner_server_id=OTHER_SERVER_ID,
        ws_session=ws,
    )

    assert ws.closed == []
    assert pool.get(RENDERER_ID) is None


@pytest.mark.asyncio
async def test_reaped_renderer_closes_the_session():
    registry, pool = make_pool()
    registry.offline_timeout_s = 0.05
    ws = FakeWs(pool)
    register(registry, ws)
    session = await pool.open(RENDERER_ID)
    closed: list = []
    session.on_closed(lambda s, reason: closed.append(reason))

    registry.disconnect(RENDERER_ID, ws, clean=False)
    pool.suspend(RENDERER_ID, ws)
    await asyncio.sleep(0.15)

    assert closed == [CloseReason.RENDERER_LOST]
    assert pool.get(RENDERER_ID) is None


@pytest.mark.asyncio
async def test_goodbye_closes_the_session():
    registry, pool = make_pool()
    ws = FakeWs(pool)
    register(registry, ws)
    session = await pool.open(RENDERER_ID)
    closed: list = []
    session.on_closed(lambda s, reason: closed.append(reason))

    registry.disconnect(RENDERER_ID, ws, clean=True)
    pool.suspend(RENDERER_ID, ws)

    assert closed == [CloseReason.RENDERER_LOST]


@pytest.mark.asyncio
async def test_stale_disconnect_does_not_suspend_the_new_connection():
    registry, pool = make_pool()
    ws = FakeWs(pool)
    register(registry, ws)
    session = await pool.open(RENDERER_ID)

    ws2 = FakeWs(pool)
    register(registry, ws2)
    await pool.reconcile(
        renderer_id=RENDERER_ID,
        reported_session_id=session.session_id,
        reported_owner_server_id=SERVER_ID,
        ws_session=ws2,
    )
    pool.suspend(RENDERER_ID, ws)  # late teardown of the replaced connection

    assert session.state is SessionState.ACTIVE
    assert session.connected


@pytest.mark.asyncio
async def test_renderer_reported_error_closes_the_session():
    registry, pool = make_pool()
    ws = FakeWs(pool)
    register(registry, ws)
    session = await pool.open(RENDERER_ID)
    closed: list = []
    session.on_closed(lambda s, reason: closed.append(reason))

    pool.handle_closed(
        RENDERER_ID,
        session_id=session.session_id,
        renderer_error=True,
        detail="alsa device gone",
    )

    assert closed == [CloseReason.RENDERER_ERROR]
    assert pool.get(RENDERER_ID) is None


@pytest.mark.asyncio
async def test_drop_while_opening_fails_the_open():
    registry, pool = make_pool(open_timeout_s=5.0)
    ws = FakeWs(pool)
    ws.answer = False
    register(registry, ws)

    task = asyncio.create_task(pool.open(RENDERER_ID))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    pool.suspend(RENDERER_ID, ws)

    with pytest.raises(SessionOpenFailed):
        await task
    assert pool.get(RENDERER_ID) is None


@pytest.mark.asyncio
async def test_shutdown_closes_every_session():
    registry, pool = make_pool()
    ws = FakeWs(pool)
    register(registry, ws)
    session = await pool.open(RENDERER_ID)

    await pool.shutdown()

    assert session.state is SessionState.CLOSED
    assert ws.closed == [(session.session_id, CloseReason.SHUTDOWN)]
    assert pool.list() == []
