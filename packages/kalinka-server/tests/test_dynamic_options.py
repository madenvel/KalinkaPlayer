"""Tests for the dynamic-options mechanism + ALSA enumerator shaping.

Two concerns covered here:

* Wire contract — fields tagged ``dynamic_options=True`` emit a
  schema entry with ``enum_values=None`` and the live option list
  lands in the values envelope.
* ALSA filtering — the noisy hint set from libasound (``front:``,
  ``surround*:``, ``sysdefault:``, ...) is reduced to a clean dropdown
  with stable ``hw:CARD=…`` / ``plughw:CARD=…`` pairs and a sticky
  "(not connected)" fallback for hot-unplug cases.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from kalinka_server.alsa_options import (
    ALSA_DEVICE_PATH,
    _build_options,
    list_alsa_options_for,
    make_alsa_resolver,
)
from kalinka_server.config_model import KalinkaConfig
from kalinka_server.config_schema_processor import (
    build_enum_options,
    build_presentation,
)
from kalinka_server.options_registry import OptionsRegistry
from kalinka_server.presentation_schema import OptionSpec


# ---------------------------------------------------------------------------
# Fake hints
# ---------------------------------------------------------------------------


@dataclass
class _FakeHint:
    name: str
    label: str = ""
    ioid: str = ""


# A representative slice of what `aplay -L` produces on a typical Linux
# desktop with HDMI + analog onboard audio + PipeWire bridge.
_TYPICAL_HINTS = [
    _FakeHint(name="null", label="Discard all samples"),
    _FakeHint(name="pipewire", label="PipeWire Sound Server"),
    _FakeHint(name="default", label="Default ALSA Output"),
    _FakeHint(name="sysdefault:CARD=sofhdadsp", label="sof-hda-dsp"),
    _FakeHint(
        name="hdmi:CARD=sofhdadsp,DEV=0",
        label="sof-hda-dsp, HDMI 1 · HDMI Audio Output",
        ioid="Output",
    ),
    _FakeHint(
        name="hw:CARD=sofhdadsp,DEV=0",
        label="sof-hda-dsp, HDA Analog",
        ioid="Output",
    ),
    _FakeHint(
        name="plughw:CARD=sofhdadsp,DEV=0",
        label="sof-hda-dsp, HDA Analog",
        ioid="Output",
    ),
    _FakeHint(
        name="hw:CARD=sofhdadsp,DEV=3",
        label="sof-hda-dsp, HDMI 1",
        ioid="Output",
    ),
    _FakeHint(
        name="plughw:CARD=sofhdadsp,DEV=3",
        label="sof-hda-dsp, HDMI 1",
        ioid="Output",
    ),
    _FakeHint(
        name="front:CARD=sofhdadsp,DEV=0",
        label="virtual front",
        ioid="Output",
    ),
    _FakeHint(
        name="surround51:CARD=sofhdadsp,DEV=0",
        label="virtual surround",
        ioid="Output",
    ),
    _FakeHint(
        name="dmix:CARD=sofhdadsp,DEV=0",
        label="direct mix",
        ioid="Output",
    ),
    _FakeHint(name="microphone", label="Input-only mic", ioid="Input"),
]


# ---------------------------------------------------------------------------
# _build_options
# ---------------------------------------------------------------------------


def test_default_is_always_first_even_without_alsa():
    out = _build_options([], current_value=None)
    assert out[0] == OptionSpec(value="default", label="System default")


def test_hw_and_plughw_pairs_survive_filtering():
    out = _build_options(_TYPICAL_HINTS, current_value=None)
    values = {o.value for o in out}
    assert "hw:CARD=sofhdadsp,DEV=0" in values
    assert "plughw:CARD=sofhdadsp,DEV=0" in values
    assert "hw:CARD=sofhdadsp,DEV=3" in values
    assert "plughw:CARD=sofhdadsp,DEV=3" in values


def test_noise_filtered_out():
    """Virtual stereo layouts and the null sink should never reach
    the user — they confuse the choice without adding value."""
    out = _build_options(_TYPICAL_HINTS, current_value=None)
    values = {o.value for o in out}
    assert "null" not in values
    assert "sysdefault:CARD=sofhdadsp" not in values
    assert "front:CARD=sofhdadsp,DEV=0" not in values
    assert "surround51:CARD=sofhdadsp,DEV=0" not in values
    assert "dmix:CARD=sofhdadsp,DEV=0" not in values


def test_input_only_devices_dropped():
    out = _build_options(_TYPICAL_HINTS, current_value=None)
    values = {o.value for o in out}
    assert "microphone" not in values


def test_pipewire_destination_kept():
    out = _build_options(_TYPICAL_HINTS, current_value=None)
    values = {o.value for o in out}
    assert "pipewire" in values


def test_plughw_label_carries_auto_convert_hint():
    out = _build_options(_TYPICAL_HINTS, current_value=None)
    by_value = {o.value: o.label for o in out}
    assert "(auto-convert)" in by_value["plughw:CARD=sofhdadsp,DEV=0"]
    assert "(auto-convert)" not in by_value["hw:CARD=sofhdadsp,DEV=0"]


def test_saved_value_not_present_is_appended_as_not_connected():
    saved = "hw:CARD=HifiBerry,DEV=0"
    out = _build_options(_TYPICAL_HINTS, current_value=saved)
    last = out[-1]
    assert last.value == saved
    assert "(not connected)" in last.label


def test_saved_value_in_live_list_not_duplicated():
    """User's current selection that IS plugged in should appear
    exactly once (no synthetic '(not connected)' duplicate)."""
    saved = "hw:CARD=sofhdadsp,DEV=0"
    out = _build_options(_TYPICAL_HINTS, current_value=saved)
    values = [o.value for o in out]
    assert values.count(saved) == 1
    assert not any("(not connected)" in o.label for o in out)


def test_options_are_ordered_deterministically():
    """Two calls on the same input must produce the same order so
    the client UI doesn't shuffle visually between refreshes."""
    a = _build_options(_TYPICAL_HINTS, current_value=None)
    b = _build_options(_TYPICAL_HINTS, current_value=None)
    assert [o.value for o in a] == [o.value for o in b]
    # Default is always pinned at index 0.
    assert a[0].value == "default"


def test_list_alsa_options_for_returns_default_when_extension_missing(
    monkeypatch,
):
    """If the native extension fails to import or raises, the
    dropdown still offers `default` — never crash the values blob."""
    import kalinka_server.alsa_options as mod
    import sys

    # Block the C extension submodule so the `from native_player.native_player
    # import ...` line inside `list_alsa_options_for` raises ImportError.
    monkeypatch.setitem(sys.modules, "native_player.native_player", None)
    out = mod.list_alsa_options_for(current_value=None)
    assert out == [OptionSpec(value="default", label="System default")]


# ---------------------------------------------------------------------------
# Schema emitter: ALSA device field surfaces in the simple page tree
# ---------------------------------------------------------------------------


def test_alsa_device_appears_in_simple_view_as_enum_dropdown():
    schema = build_presentation(KalinkaConfig(), {}, {})
    alsa_field = None
    for page in schema.pages:
        for section in page.sections:
            for f in section.fields:
                if f.path == ALSA_DEVICE_PATH:
                    alsa_field = f
                    break
    assert alsa_field is not None, (
        f"{ALSA_DEVICE_PATH} should appear in the simple page tree"
    )
    assert alsa_field.widget.value == "enum_dropdown"
    # Options ship in the values envelope under enum_options[path]
    # rather than in the schema's enum_values — keeps schema_version
    # stable across hot-plug. The client renders based on whichever
    # is present.
    assert alsa_field.enum_values is None


# ---------------------------------------------------------------------------
# build_enum_options + registry
# ---------------------------------------------------------------------------


def test_build_enum_options_returns_resolved_lists():
    registry = OptionsRegistry()
    registry.register(
        "x.path",
        lambda: [OptionSpec(value="a", label="A"), {"value": "b", "label": "B"}],
    )
    out = asyncio.run(build_enum_options(registry))
    assert out["x.path"] == [
        OptionSpec(value="a", label="A"),
        OptionSpec(value="b", label="B"),
    ]


def test_build_enum_options_skips_resolver_that_raises(caplog):
    registry = OptionsRegistry()

    def boom():
        raise RuntimeError("nope")

    registry.register("broken", boom)
    with caplog.at_level("ERROR"):
        out = asyncio.run(build_enum_options(registry))
    assert "broken" not in out
    assert "raised" in caplog.text


def test_build_enum_options_handles_async_resolver():
    registry = OptionsRegistry()

    async def aresolve():
        return [{"value": "x", "label": "X"}]

    registry.register("async.path", aresolve)
    out = asyncio.run(build_enum_options(registry))
    assert out["async.path"] == [OptionSpec(value="x", label="X")]


# ---------------------------------------------------------------------------
# make_alsa_resolver — closes over a live config reference
# ---------------------------------------------------------------------------


def test_resolver_re_reads_current_value_each_call(monkeypatch):
    """The resolver must NOT capture the device value at registration
    time — config mutates after PUT /server/config, and the next call
    must reflect the new value when deciding whether to append a
    '(not connected)' entry for the prior selection.
    """
    import kalinka_server.alsa_options as mod

    box = {"current": "hw:CARD=ORIGINAL,DEV=0"}
    resolver = make_alsa_resolver(lambda: box["current"])

    # Pretend ALSA emits only the analog output; the user's pick is
    # not present, so the resolver must add the "(not connected)"
    # sentinel for whichever string `get_current_value` returns *now*.
    monkeypatch.setattr(
        mod,
        "list_alsa_options_for",
        lambda current: _build_options(
            [
                _FakeHint(
                    name="hw:CARD=onboard,DEV=0",
                    label="Onboard",
                    ioid="Output",
                ),
            ],
            current_value=current,
        ),
    )

    first = resolver()
    assert any("ORIGINAL" in o.value for o in first), (
        "first call should see the original selection"
    )

    box["current"] = "hw:CARD=CHANGED,DEV=0"
    second = resolver()
    assert any("CHANGED" in o.value for o in second), (
        "after the user picks something else, the resolver must "
        "re-read so the '(not connected)' fallback reflects the new "
        "saved value rather than the stale one"
    )
    assert not any("ORIGINAL" in o.value for o in second)
