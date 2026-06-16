"""Tests for concurrent plugin setup in
``PreparedModuleCollection._scan_and_setup_plugins_from_entry_points``.

A slow plugin ``setup()`` (network login, web-bundle download, subprocess
spawn) used to serialise every other plugin behind it, delaying the server
from accepting connections. Setup now fans out with ``asyncio.gather``. These
tests pin down that the calls overlap, that results come back in entry-point
order (so the default-module choice stays deterministic), and that one
plugin's failure is isolated.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from pydantic import Field

from kalinka_plugin_sdk.module_config import ModuleConfig
from kalinka_plugin_sdk.plugin import PluginBase, PluginType
from kalinka_plugin_sdk import ModuleHealthState

from kalinka_server.player_setup import PreparedModuleCollection


class _Config(ModuleConfig):
    enabled: bool = Field(default=True)


def _make_plugin(plugin_id: str, *, delay: float = 0.0, fail: bool = False):
    """Build a fake input-module plugin class whose setup() optionally sleeps
    (to expose serialisation) and/or raises."""

    started: list[float] = []

    class _Plugin(PluginBase):
        PLUGIN_ID = plugin_id
        REQUIRES_SDK = "1.0"
        PLUGIN_TYPE = PluginType.INPUT_MODULE
        CONFIG_MODEL = _Config

        async def setup(self, context):
            started.append(time.monotonic())
            if delay:
                await asyncio.sleep(delay)
            if fail:
                raise RuntimeError(f"{plugin_id} boom")

        def get_interface(self):
            return None

    _Plugin._started = started  # type: ignore[attr-defined]
    return _Plugin


def _collection(plugins) -> PreparedModuleCollection:
    """A collection whose entry-point scan yields the given plugin classes and
    whose plugin-context builder is stubbed (we only exercise the setup flow)."""
    collection = PreparedModuleCollection()
    collection._scan_entry_points = lambda: iter(  # type: ignore[method-assign]
        [(p.PLUGIN_ID, p) for p in plugins]
    )
    # PreparedPlugin.setup reads plugin_context.config.enabled; the rest of the
    # real context (playqueue/eventbus) is irrelevant to the setup flow here.
    collection._make_plugin_context = (  # type: ignore[method-assign]
        lambda name, cls, config: SimpleNamespace(config=config)
    )
    return collection


@pytest.mark.asyncio
async def test_setups_run_concurrently():
    # Three plugins that each sleep 0.2s. Serialised that is ~0.6s; concurrent
    # is ~0.2s. Assert well under the serial sum.
    plugins = [_make_plugin(f"p{i}", delay=0.2) for i in range(3)]
    collection = _collection(plugins)

    start = time.monotonic()
    result = await collection._scan_and_setup_plugins_from_entry_points({})
    elapsed = time.monotonic() - start

    assert [name for name, _ in result] == ["p0", "p1", "p2"]
    assert all(p.health_state == ModuleHealthState.READY for _, p in result)
    assert elapsed < 0.45, f"setup did not overlap (took {elapsed:.2f}s)"


@pytest.mark.asyncio
async def test_order_preserved_regardless_of_completion():
    # The last plugin finishes first, the first finishes last — result order
    # must still follow entry-point order so "first enabled" is deterministic.
    plugins = [
        _make_plugin("slow", delay=0.3),
        _make_plugin("medium", delay=0.15),
        _make_plugin("fast", delay=0.01),
    ]
    collection = _collection(plugins)

    result = await collection._scan_and_setup_plugins_from_entry_points({})

    assert [name for name, _ in result] == ["slow", "medium", "fast"]


@pytest.mark.asyncio
async def test_one_failure_is_isolated():
    plugins = [
        _make_plugin("ok1"),
        _make_plugin("broken", fail=True),
        _make_plugin("ok2"),
    ]
    collection = _collection(plugins)

    result = await collection._scan_and_setup_plugins_from_entry_points({})
    by_name = dict(result)

    assert [name for name, _ in result] == ["ok1", "broken", "ok2"]
    assert by_name["ok1"].health_state == ModuleHealthState.READY
    assert by_name["ok2"].health_state == ModuleHealthState.READY
    assert by_name["broken"].health_state == ModuleHealthState.ERROR
    assert "boom" in (by_name["broken"].error_message or "")


@pytest.mark.asyncio
async def test_disabled_plugin_is_not_set_up():
    plugin = _make_plugin("off")
    collection = _collection([plugin])
    overrides = {"input_modules.off.enabled": False}

    result = await collection._scan_and_setup_plugins_from_entry_points(overrides)
    by_name = dict(result)

    assert by_name["off"].health_state == ModuleHealthState.DISABLED
    assert plugin._started == []  # setup() never called
