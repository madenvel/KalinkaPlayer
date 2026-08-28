"""run_discovery must not run the blocking SSDP scan on the event loop.

discover_musiccast_devices blocks for discovery_timeout seconds per interface
(sync sockets + sync HTTP); executed on the loop it froze the whole server for
that long on every retry while the device was offline.
"""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import MagicMock

from kalinka_plugin_musiccast import musiccast
from kalinka_plugin_musiccast.config_model import KalinkaPluginMusiccastConfig
from kalinka_plugin_musiccast.musiccast import KalinkaPluginMusiccastDevice


@pytest.fixture
def device():
    config = KalinkaPluginMusiccastConfig(
        connected_input="netusb",
        zone_name="main",
    )
    return KalinkaPluginMusiccastDevice(config, MagicMock(), MagicMock())


@pytest.mark.unit
async def test_discovery_runs_off_the_event_loop(device, monkeypatch):
    seen: dict[str, bool] = {}

    def fake_discover(iface, timeout_seconds):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return None

    monkeypatch.setattr(
        musiccast, "get_network_interfaces", lambda: [("eth0", "192.168.1.2")]
    )
    monkeypatch.setattr(musiccast, "discover_musiccast_devices", fake_discover)

    assert await device.run_discovery() is False
    assert seen == {"on_loop": False}


@pytest.mark.unit
async def test_discovery_result_still_connects(device, monkeypatch):
    monkeypatch.setattr(
        musiccast, "get_network_interfaces", lambda: [("eth0", "192.168.1.2")]
    )
    monkeypatch.setattr(
        musiccast,
        "discover_musiccast_devices",
        lambda iface, timeout_seconds: {"api_base_url": "http://10.0.0.5/yxc/"},
    )

    async def fake_get_ready():
        device.ready = True

    monkeypatch.setattr(device, "get_ready", fake_get_ready)

    assert await device.run_discovery() is True
    assert device.base_url == "http://10.0.0.5/yxc/"
