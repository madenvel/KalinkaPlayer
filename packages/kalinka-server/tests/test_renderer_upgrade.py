"""Upgrading a renderer that usually lives on another machine.

The connection is often the only way to reach it, so the rules that matter
here are the ones that decide whether it is asked at all: never mid-playback,
never a build that cannot replace itself, and — the point of the whole
mechanism — even when its protocol no longer matches this Core's.
"""

import asyncio

import pytest

from kalinka_server.renderer_proto import renderer_pb2 as pb
from kalinka_server.renderer_registry import RendererRegistry, RendererUnavailable
from kalinka_server.renderer_upgrade import (
    RendererUpgradeService,
    UpgradeRefused,
    version_is_newer,
)


class FakeLink:
    """Answers an upgrade request the way a renderer's session would."""

    def __init__(self, accepted: bool = True, detail: str = "upgrading"):
        self.accepted = accepted
        self.detail = detail
        self.requested: list[str] = []
        self.service: RendererUpgradeService | None = None
        self.renderer_id = "rid-1"
        self._id = 0

    def next_message_id(self) -> int:
        self._id += 1
        return self._id

    async def send_upgrade(self, message_id: int, target_version: str) -> None:
        self.requested.append(target_version)
        result = pb.UpgradeResult(accepted=self.accepted, detail=self.detail)
        assert self.service is not None
        self.service.handle_reply(self.renderer_id, message_id, result)


def _registry_with(
    *,
    version: str = "0.3.0",
    compatible: bool = True,
    upgrade_supported: bool = True,
    kind: str = "native",
    link=None,
    busy: bool = False,
) -> tuple[RendererRegistry, RendererUpgradeService, FakeLink]:
    registry = RendererRegistry()
    service = RendererUpgradeService(registry, lambda _: busy, timeout_s=1.0)
    link = link or FakeLink()
    link.service = service
    registry.register(
        renderer_id="rid-1",
        instance_id="inst-1",
        friendly_name="Attic",
        software_version=version,
        kind=kind,
        platform={"os": "linux"},
        session=link,
        compatible=compatible,
        upgrade_supported=upgrade_supported,
    )
    return registry, service, link


async def test_the_renderer_is_told_which_release_to_install():
    _, service, link = _registry_with()

    detail = await service.upgrade("rid-1", "0.4.0")

    assert link.requested == ["0.4.0"]
    assert detail == "upgrading"


async def test_a_renderer_we_cannot_drive_can_still_be_upgraded():
    """The whole reason the upgrade rides the raw link: a renderer left behind
    by a protocol bump is exactly the one that has to be replaced."""
    _, service, link = _registry_with(compatible=False)

    await service.upgrade("rid-1", "0.4.0")

    assert link.requested == ["0.4.0"]


async def test_a_playing_renderer_is_left_alone():
    _, service, link = _registry_with(busy=True)

    with pytest.raises(UpgradeRefused):
        await service.upgrade("rid-1", "0.4.0")
    assert link.requested == []


async def test_a_build_that_cannot_replace_itself_is_never_asked():
    _, service, link = _registry_with(upgrade_supported=False)

    with pytest.raises(UpgradeRefused):
        await service.upgrade("rid-1", "0.4.0")
    assert link.requested == []


async def test_a_refusal_from_the_renderer_is_reported_not_swallowed():
    _, service, _ = _registry_with(link=FakeLink(accepted=False, detail="busy"))

    with pytest.raises(UpgradeRefused, match="busy"):
        await service.upgrade("rid-1", "0.4.0")


async def test_refusals_name_the_renderer_rather_than_its_id():
    """These reach a person, who knows the renderer as 'Attic' and has no way
    to read a uuid off a toast."""
    _, service, _ = _registry_with(upgrade_supported=False)

    with pytest.raises(UpgradeRefused) as refusal:
        await service.upgrade("rid-1", "0.4.0")
    assert "Attic" in str(refusal.value)
    assert "rid-1" not in str(refusal.value)


async def test_an_absent_renderer_cannot_be_upgraded():
    registry, service, _ = _registry_with()
    registry.disconnect("rid-1", registry.get("rid-1").session, clean=True)

    with pytest.raises(RendererUnavailable):
        await service.upgrade("rid-1", "0.4.0")


async def test_a_disconnect_mid_request_fails_the_wait():
    class SilentLink(FakeLink):
        async def send_upgrade(self, message_id: int, target_version: str) -> None:
            self.requested.append(target_version)

    registry, service, link = _registry_with(link=SilentLink())
    task = asyncio.ensure_future(service.upgrade("rid-1", "0.4.0"))
    await asyncio.sleep(0)
    service.handle_disconnect("rid-1")

    with pytest.raises(RendererUnavailable):
        await task


class TestCandidates:
    def _service(self, **kwargs):
        return _registry_with(**kwargs)[1]

    async def test_a_behind_renderer_is_a_candidate(self):
        service = self._service(version="0.3.0")
        assert [c.renderer_id for c in service.candidates("0.4.0")] == ["rid-1"]

    async def test_a_candidate_says_whether_it_is_playing(self):
        """Listed either way: the Core decides whether to wait for it."""
        service = self._service(version="0.3.0", busy=True)
        assert [c.busy for c in service.candidates("0.4.0")] == [True]

    async def test_a_current_renderer_is_not(self):
        service = self._service(version="0.4.0")
        assert service.candidates("0.4.0") == []

    async def test_a_browser_renderer_is_not(self):
        """It upgrades by reloading the page, not by installing a package."""
        service = self._service(kind="web")
        assert service.candidates("0.4.0") == []

    async def test_no_known_release_means_no_candidates(self):
        """Silence about what is published is not a verdict on what is stale."""
        service = self._service()
        assert service.candidates(None) == []


    async def test_a_renderer_that_cannot_upgrade_itself_is_stranded_not_queued(
        self,
    ):
        """Reported so it can be said out loud, and left out of the work list:
        waiting for it would hold every other upgrade back for ever."""
        service = self._service(version="0.3.0", upgrade_supported=False)
        assert service.candidates("0.4.0") == []
        assert [c.renderer_id for c in service.stranded("0.4.0")] == ["rid-1"]

    async def test_one_that_can_upgrade_is_not_stranded(self):
        service = self._service(version="0.3.0")
        assert service.stranded("0.4.0") == []

class TestVersionOrdering:
    """Judged the same for any packaging: the deb rules do not apply to an rpm
    host, and both sides carry plain releases."""

    def test_a_later_release_wins(self):
        assert version_is_newer("0.4.0", "0.3.0")
        assert version_is_newer("0.10.0", "0.9.0")

    def test_the_same_release_is_not_newer(self):
        assert not version_is_newer("0.4.0", "0.4.0")

    def test_an_earlier_release_is_not_newer(self):
        assert not version_is_newer("0.3.0", "0.4.0")

    def test_a_release_outranks_the_development_build_leading_to_it(self):
        assert version_is_newer("0.4.0", "0.4.0~dev3+g1a2b3c4")

    def test_an_unknown_version_is_never_ranked(self):
        assert not version_is_newer("0.4.0", "")
        assert not version_is_newer("", "0.3.0")


class TestBringForward:
    """The Core asks this before upgrading itself, so 'nothing left to do' is
    the only answer that lets it move."""

    async def test_a_renderer_that_is_current_needs_nothing(self):
        _, service, link = _registry_with(version="0.4.0")
        assert await service.bring_forward("0.4.0") is True
        assert link.requested == []

    async def test_a_behind_renderer_is_upgraded_and_the_caller_waits(self):
        _, service, link = _registry_with(version="0.3.0")
        assert await service.bring_forward("0.4.0") is False
        assert link.requested == ["0.4.0"]

    async def test_a_playing_renderer_is_left_for_later(self):
        _, service, link = _registry_with(version="0.3.0", busy=True)
        assert await service.bring_forward("0.4.0") is False
        assert link.requested == []

    async def test_one_that_cannot_upgrade_itself_is_not_waited_for(self):
        """No later attempt would change it, so holding the Core back for ever
        buys nothing."""
        _, service, link = _registry_with(
            version="0.3.0", upgrade_supported=False
        )
        assert await service.bring_forward("0.4.0") is True
        assert link.requested == []

    async def test_a_refusal_does_not_stop_the_caller_looking_again(self):
        _, service, _ = _registry_with(
            version="0.3.0", link=FakeLink(accepted=False, detail="busy")
        )
        assert await service.bring_forward("0.4.0") is False

    async def test_nothing_is_done_while_no_release_is_known(self):
        _, service, link = _registry_with(version="0.3.0")
        assert await service.bring_forward(None) is True
        assert link.requested == []
