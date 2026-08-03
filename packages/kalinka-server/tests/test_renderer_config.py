import asyncio

import pytest

from kalinka_server.renderer_config import RendererConfigService
from kalinka_server.renderer_proto import renderer_pb2 as pb
from kalinka_server.renderer_registry import RendererRegistry
from kalinka_server.renderer_sessions import RendererUnavailable

RENDERER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class FakeWs:
    """Renderer end of the config plane, answering in line like the real one."""

    def __init__(self, service=None):
        self.service = service
        self.requests = 0
        self.updates: list[dict] = []
        self.answer = True
        self.device_options = ["default", "hw:CARD=sofhdadsp,DEV=0"]
        self.values = {"output.driver": "alsa", "output.device": "default"}
        self._next_id = 0

    def next_message_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _snapshot(self) -> pb.ConfigSnapshot:
        snapshot = pb.ConfigSnapshot()
        snapshot.config_version = f"v{len(self.device_options)}"
        section = snapshot.sections.add()
        section.path = "output"
        section.title = "Output"
        device = section.fields.add()
        device.path = "output.device"
        device.title = "Device"
        device.type = pb.CONFIG_FIELD_TYPE_ENUM
        device.value = self.values["output.device"]
        device.default_value = "default"
        device.apply = pb.APPLY_COST_INTERRUPTS_PLAYBACK
        for name in self.device_options:
            option = device.options.add()
            option.value = name
            option.label = name
        return snapshot

    async def send_config_request(self, message_id) -> None:
        self.requests += 1
        if self.answer:
            self.service.handle_reply(RENDERER_ID, message_id, self._snapshot())

    async def send_config_update(self, message_id, changes) -> None:
        self.updates.append(dict(changes))
        if not self.answer:
            return
        result = pb.ConfigResult()
        for path, value in changes.items():
            outcome = result.outcomes.add()
            outcome.path = path
            if path in self.values and value in self.device_options:
                self.values[path] = value
                outcome.applied = True
                outcome.value = value
                result.effect = pb.APPLY_COST_INTERRUPTS_PLAYBACK
            else:
                outcome.value = self.values.get(path, "")
                outcome.error = "not one of the offered options"
        result.config_version = f"v{len(self.device_options)}"
        self.service.handle_reply(RENDERER_ID, message_id, result)

    async def replace(self):
        pass


def make_service(timeout_s=5.0):
    registry = RendererRegistry(offline_timeout_s=30.0)
    service = RendererConfigService(registry, timeout_s=timeout_s)
    ws = FakeWs(service)
    registry.register(
        renderer_id=RENDERER_ID,
        instance_id="inst-1",
        friendly_name="Test Renderer",
        software_version="0.1.0",
        kind="native",
        platform={},
        session=ws,
    )
    return registry, service, ws


@pytest.mark.asyncio
async def test_config_arrives_as_schema_and_values_together():
    registry, service, ws = make_service()

    config = await service.get(RENDERER_ID)

    assert config["config_version"] == "v2"
    field = config["sections"][0]["fields"][0]
    assert field["path"] == "output.device"
    assert field["type"] == "enum"
    assert field["value"] == "default"
    assert field["apply"] == "interrupts_playback"
    assert [o["value"] for o in field["options"]] == [
        "default",
        "hw:CARD=sofhdadsp,DEV=0",
    ]


@pytest.mark.asyncio
async def test_the_device_list_is_as_fresh_as_the_request():
    registry, service, ws = make_service()
    await service.get(RENDERER_ID)

    ws.device_options.append("hw:CARD=USB,DEV=0")  # a DAC gets plugged in
    config = await service.get(RENDERER_ID)

    assert config["config_version"] == "v3"
    assert len(config["sections"][0]["fields"][0]["options"]) == 3


@pytest.mark.asyncio
async def test_an_update_reports_what_took_effect():
    registry, service, ws = make_service()

    result = await service.update(
        RENDERER_ID, {"output.device": "hw:CARD=sofhdadsp,DEV=0"}
    )

    assert ws.updates == [{"output.device": "hw:CARD=sofhdadsp,DEV=0"}]
    assert result["effect"] == "interrupts_playback"
    assert result["outcomes"] == [
        {
            "path": "output.device",
            "applied": True,
            "value": "hw:CARD=sofhdadsp,DEV=0",
            "error": "",
        }
    ]


@pytest.mark.asyncio
async def test_a_refused_value_leaves_the_old_one_in_the_answer():
    registry, service, ws = make_service()

    result = await service.update(RENDERER_ID, {"output.device": "hw:CARD=gone"})

    assert result["outcomes"][0]["applied"] is False
    assert result["outcomes"][0]["error"] == "not one of the offered options"
    assert result["outcomes"][0]["value"] == "default"


@pytest.mark.asyncio
async def test_config_needs_a_connected_renderer():
    registry, service, ws = make_service()
    registry.disconnect(RENDERER_ID, ws, clean=False)

    with pytest.raises(RendererUnavailable):
        await service.get(RENDERER_ID)
    with pytest.raises(RendererUnavailable):
        await service.get("nobody-by-that-id")


@pytest.mark.asyncio
async def test_a_disconnect_fails_the_request_in_flight():
    registry, service, ws = make_service()
    ws.answer = False

    task = asyncio.create_task(service.get(RENDERER_ID))
    await asyncio.sleep(0)
    service.handle_disconnect(RENDERER_ID)

    with pytest.raises(RendererUnavailable):
        await task


@pytest.mark.asyncio
async def test_a_silent_renderer_times_out():
    registry, service, ws = make_service(timeout_s=0.05)
    ws.answer = False

    with pytest.raises(asyncio.TimeoutError):
        await service.get(RENDERER_ID)


@pytest.mark.asyncio
async def test_a_reply_nobody_awaits_is_dropped():
    registry, service, ws = make_service()

    service.handle_reply(RENDERER_ID, 999, pb.ConfigSnapshot())  # no raise

    config = await service.get(RENDERER_ID)
    assert config["config_version"] == "v2"
