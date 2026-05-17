"""ALSA PCM device enumeration for the settings dropdown.

The native_player C++ extension exposes
``native_player.list_alsa_pcm_devices()`` which returns every hint
ALSA itself publishes (the equivalent of ``aplay -L``). This module
filters and shapes that raw stream into the option list the UI shows
under ``base_config.output.alsa.device``.

Filtering rationale
-------------------
- ``default``: always include first, labelled clearly — many setups
  rely on the system default (PipeWire/PulseAudio bridge, ALSA's own
  asound.conf), and surfacing it explicitly avoids the "where did my
  device go?" question.
- ``hw:CARD=<id>,DEV=<n>``: included. Stable identifier — survives
  card-index shuffling on kernel upgrade because it's keyed on the
  driver's text id rather than a transient number.
- ``plughw:CARD=<id>,DEV=<n>``: included as a sibling of each ``hw:``
  entry, labelled ``(auto-convert)`` so the user can pick the
  format-conversion variant when their DAC is picky about sample
  rates. Doubling the list is acceptable because the labels make the
  difference obvious.
- ``sysdefault:CARD=<id>``: redundant with ``hw:CARD=<id>,DEV=0`` in
  most cases; skipped to keep the list compact.
- ``front:``/``surround*:``/``iec958:``/``dmix:``/``dsnoop:``: virtual
  layouts ALSA derives from the hw devices. Confusing for a music
  player and rarely the user's intent; skipped.
- ``null``: present in every system but useless for music; skipped.
- ``pipewire``/``pulse``: included if present — they're legitimate
  destinations for users running a sound server.

A device that's saved in config but not in the live hint list is
appended as ``"<value> (not connected)"`` so the user sees what's
stored without losing it on a hot-unplug, mirroring macOS Sound
preferences behaviour.
"""

from __future__ import annotations

import logging
from typing import Iterable

from .presentation_schema import OptionSpec

logger = logging.getLogger(__name__.split(".")[-1])


# Path the ALSA enumerator binds to in the options registry.
ALSA_DEVICE_PATH = "base_config.output.alsa.device"

# Hint names that are noise for a music-player UI — see the module
# docstring for the rationale. Match by name prefix.
_NOISY_PREFIXES = (
    "front:",
    "rear:",
    "center_lfe:",
    "side:",
    "surround",
    "iec958:",
    "spdif:",
    "dmix:",
    "dsnoop:",
    "hw:",  # raw hw without CARD= form is handled separately below
    "sysdefault:",
    "null",
)

# Hints we want to surface even though they don't carry a CARD= form.
_NAMED_DESTINATIONS = ("pipewire", "pulse", "jack")


def _is_output(ioid: str) -> bool:
    # Empty IOID = both directions. Skip explicit-input-only entries.
    return ioid == "" or ioid == "Output"


def _is_card_handle(name: str) -> bool:
    """True for `hw:CARD=…,DEV=…` / `plughw:CARD=…,DEV=…` — the
    stable forms we keep regardless of the noise filter.
    """
    return ("CARD=" in name) and (
        name.startswith("hw:") or name.startswith("plughw:")
    )


def _is_noisy(name: str) -> bool:
    for prefix in _NOISY_PREFIXES:
        if name.startswith(prefix) and not _is_card_handle(name):
            return True
    return False


def _label_for(name: str, raw_desc: str) -> str:
    """Shape a one-line label suitable for the dropdown.

    The C++ side already collapsed multi-line ALSA descriptions into
    a "x · y" string; we only add a short suffix for the plughw
    variant so the user can distinguish "HDMI 1" from
    "HDMI 1 (auto-convert)" at a glance.
    """
    suffix = " (auto-convert)" if name.startswith("plughw:") else ""
    desc = raw_desc.strip() or name
    # Some descriptions have a trailing ", " from an empty pcm name —
    # tidy that up so it doesn't render as "Card name, " with a
    # dangling comma.
    desc = desc.rstrip(", ").rstrip()
    return f"{desc}{suffix}"


def _build_options(
    raw_devices: Iterable, current_value: str | None
) -> list[OptionSpec]:
    """Produce the filtered, ordered option list. Pure function — the
    raw enumeration is passed in so the filtering logic is testable
    without a real ALSA system."""
    out: list[OptionSpec] = []
    seen: set[str] = set()

    def add(value: str, label: str) -> None:
        if value in seen:
            return
        seen.add(value)
        out.append(OptionSpec(value=value, label=label))

    # 1) Pin the default first, regardless of whether ALSA emitted it.
    add("default", "System default")

    # 2) Walk hints. Bucket by category so we can emit deterministic
    #    order: named destinations (pipewire/pulse), then per-card
    #    handles (hw + plughw paired).
    named: list[tuple[str, str]] = []
    card_handles: list[tuple[str, str]] = []
    for d in raw_devices:
        name = d.name
        if not _is_output(d.ioid):
            continue
        if name == "default":
            continue
        if name in _NAMED_DESTINATIONS:
            named.append((name, _label_for(name, d.label or name)))
            continue
        if _is_card_handle(name):
            card_handles.append((name, _label_for(name, d.label)))
            continue
        if _is_noisy(name):
            continue
        # Anything else falls through unfiltered — better to show a
        # rare-but-real device than to silently hide it.
        card_handles.append((name, _label_for(name, d.label or name)))

    # Named destinations alphabetised so order doesn't drift between
    # boots if ALSA reorders its hint list.
    for value, label in sorted(named):
        add(value, label.title() if value == label else label)

    # Card handles: sort by (label) so paired hw/plughw entries land
    # adjacent to each other on the same card+pcm.
    for value, label in sorted(card_handles, key=lambda e: (e[1], e[0])):
        add(value, label)

    # 3) If the user's saved value isn't in the live list, append a
    #    synthetic entry so they can see it and switch away without
    #    losing the original. Mirrors macOS Sound preferences.
    if current_value and current_value not in seen:
        add(current_value, f"{current_value} (not connected)")

    return out


def list_alsa_options_for(current_value: str | None) -> list[OptionSpec]:
    """Resolve the live option list for the ALSA device dropdown.

    Returns just ``{default}`` if the native enumerator is
    unavailable (e.g. tests on a host without libasound at import
    time, or a future container that ships without ALSA).
    """
    try:
        from native_player.native_player import (  # type: ignore
            list_alsa_pcm_devices,
        )
    except Exception as exc:  # noqa: BLE001 — extension may be absent
        logger.warning(
            "native_player.list_alsa_pcm_devices unavailable (%s); "
            "ALSA dropdown will offer only the default device",
            exc,
        )
        return _build_options([], current_value)
    try:
        raw = list_alsa_pcm_devices()
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.exception(
            "ALSA enumeration call raised; falling back to default: %s",
            exc,
        )
        raw = []
    return _build_options(raw, current_value)


def make_alsa_resolver(get_current_value):
    """Create a callable suitable for OptionsRegistry.register.

    ``get_current_value`` is a no-arg callable that returns the
    config's current ``output.alsa.device`` string. Passed indirectly
    so the resolver always sees the freshest in-memory value (the
    config object mutates after PUT /server/config).
    """
    def resolver() -> list[OptionSpec]:
        return list_alsa_options_for(get_current_value())
    return resolver
