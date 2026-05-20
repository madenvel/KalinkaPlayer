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
import re
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


# The C++ enumerator joins ALSA's multi-line DESC field (card name
# on line 1, PCM-mode description on line 2) with this exact UTF-8
# sequence so the wire format is one line per hint. Splitting on the
# same sequence here recovers the two parts for the two-line dropdown
# row — line 1 stays as the prominent label, line 2 becomes the
# dimmed description.
_DESC_JOIN = " · "  # " · "


# Matches snake_case / kebab-case identifiers — the shape ALSA uses
# when the card's "long name" is just the kernel driver string
# (``sof-hda-dsp``, ``snd_rpi_hifiberry_digi``). The connection ID is
# already encoded in the ``hw:CARD=...`` value, so repeating the
# driver name in the label only adds clutter. Anything with spaces or
# uppercase characters is treated as a human-readable name and kept.
_DRIVER_LIKE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")


def _prune_card_label(label: str) -> str:
    """Strip the redundant driver-id prefix some ALSA cards report.

    ALSA's first DESC line is conventionally ``<card_long_name>,
    <pcm_name>``. On many drivers (HDA-SOF, HiFiBerry overlays) the
    long name is the kernel module string, e.g. ``sof-hda-dsp, HDMI
    1`` — the bit on the right is the part the user actually picks
    by. When the left side looks like a driver identifier we drop it
    so the label reads as ``HDMI 1``. When ALSA duplicates the same
    string on both sides (``bcm2835 Headphones, bcm2835 Headphones``)
    we collapse to one copy.
    """
    parts = [p.strip() for p in label.split(",", 1)]
    if len(parts) != 2 or not parts[1]:
        return label
    left, right = parts
    if left == right:
        return left
    if _DRIVER_LIKE.match(left):
        return right
    return label


def _label_for(name: str, raw_desc: str) -> tuple[str, str | None]:
    """Shape a (label, description) pair suitable for the dropdown.

    The label is the short device name that fits in the collapsed
    trigger row; the description is a second-line detail (PCM mode
    name, ``auto-convert`` for the plughw variant) shown dimmed when
    the bottom sheet is open. Returning ``None`` for description lets
    the renderer drop the second line entirely on terse entries like
    ``pipewire``.
    """
    desc = raw_desc.strip() or name
    parts = desc.split(_DESC_JOIN, 1)
    # Some ALSA descriptions have a trailing ", " from an empty pcm
    # name — tidy each side so it doesn't render as "Card name, "
    # with a dangling comma.
    label = _prune_card_label(parts[0].rstrip(", ").rstrip())
    detail = parts[1].rstrip(", ").rstrip() if len(parts) > 1 else ""
    if name.startswith("plughw:"):
        detail = f"{detail} · auto-convert" if detail else "auto-convert"
    return label, (detail or None)


def _build_options(
    raw_devices: Iterable, current_value: str | None
) -> list[OptionSpec]:
    """Produce the filtered, ordered option list. Pure function — the
    raw enumeration is passed in so the filtering logic is testable
    without a real ALSA system."""
    out: list[OptionSpec] = []
    seen: set[str] = set()

    def add(value: str, label: str, description: str | None = None) -> None:
        if value in seen:
            return
        seen.add(value)
        out.append(
            OptionSpec(value=value, label=label, description=description)
        )

    # 1) Pin the default first, regardless of whether ALSA emitted it.
    add("default", "System default")

    # 2) Walk hints. Bucket by category so we can emit deterministic
    #    order: named destinations (pipewire/pulse), then per-card
    #    handles (hw + plughw paired).
    named: list[tuple[str, str, str | None]] = []
    card_handles: list[tuple[str, str, str | None]] = []
    for d in raw_devices:
        name = d.name
        if not _is_output(d.ioid):
            continue
        if name == "default":
            continue
        if name in _NAMED_DESTINATIONS:
            label, detail = _label_for(name, d.label or name)
            named.append((name, label, detail))
            continue
        if _is_card_handle(name):
            label, detail = _label_for(name, d.label)
            card_handles.append((name, label, detail))
            continue
        if _is_noisy(name):
            continue
        # Anything else falls through unfiltered — better to show a
        # rare-but-real device than to silently hide it.
        label, detail = _label_for(name, d.label or name)
        card_handles.append((name, label, detail))

    # Named destinations alphabetised so order doesn't drift between
    # boots if ALSA reorders its hint list.
    for value, label, detail in sorted(named, key=lambda e: e[0]):
        add(value, label.title() if value == label else label, detail)

    # Card handles: sort by (label, description) so paired hw/plughw
    # entries land adjacent on the same card+pcm — hw first because
    # its description sorts before the "… · auto-convert" plughw one.
    for value, label, detail in sorted(
        card_handles, key=lambda e: (e[1], e[2] or "", e[0])
    ):
        add(value, label, detail)

    # 3) If the user's saved value isn't in the live list, append a
    #    synthetic entry so they can see it and switch away without
    #    losing the original. Mirrors macOS Sound preferences. The
    #    "not connected" note goes in description so the trigger row
    #    still shows the raw handle alone.
    if current_value and current_value not in seen:
        add(current_value, current_value, "not connected")

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
