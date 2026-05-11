"""Registry of plugin-declared dynamic fields.

Built once per plugin lifecycle. Each entry maps a full dotted config
path (e.g. "input_modules.localfiles.searcher.status") to the plugin
instance that resolves it plus the declared metadata.

The registry is consumed in three places:

  - schema build: inject FieldSpec entries (marked dynamic=True) into
    the matching nested section.
  - values build: call ``plugin_instance.resolve_dynamic_field(subpath)``
    for each entry and splice the result into the values blob.
  - PUT /server/config: reject writes to any path that's in the registry.

Failures to resolve a single dynamic field do not fail the request — the
value is omitted with a log line so the rest of the values blob remains
usable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from kalinka_plugin_sdk import DynamicFieldDecl
from kalinka_plugin_sdk.plugin import PluginBase

from .player_setup import PreparedPlugin


logger = logging.getLogger(__name__.split(".")[-1])


@dataclass(frozen=True)
class DynamicFieldEntry:
    """An entry in the dynamic-field registry."""

    full_path: str  # e.g. "input_modules.localfiles.searcher.status"
    plugin_id: str  # e.g. "localfiles"
    kind: str  # "input_module" or "device"
    plugin_instance: PluginBase
    subpath: str  # key passed to plugin_instance.resolve_dynamic_field, e.g. "searcher.status"
    decl: DynamicFieldDecl


def _collect(
    prefix: str,
    kind: str,
    plugin_id: str,
    prepared: PreparedPlugin,
    registry: dict[str, DynamicFieldEntry],
) -> None:
    if prepared.plugin_instance is None:
        return
    declared = getattr(prepared.plugin_class, "DYNAMIC_FIELDS", None) or {}
    for subpath, decl in declared.items():
        if not isinstance(decl, DynamicFieldDecl):
            logger.warning(
                "Plugin %s declared dynamic field %r with %r; expected "
                "DynamicFieldDecl, ignoring",
                plugin_id,
                subpath,
                type(decl).__name__,
            )
            continue
        full_path = f"{prefix}.{plugin_id}.{subpath}"
        if full_path in registry:
            logger.warning(
                "Dynamic field path %r already registered; ignoring duplicate "
                "from plugin %s",
                full_path,
                plugin_id,
            )
            continue
        registry[full_path] = DynamicFieldEntry(
            full_path=full_path,
            plugin_id=plugin_id,
            kind=kind,
            plugin_instance=prepared.plugin_instance,
            subpath=subpath,
            decl=decl,
        )


def build_dynamic_field_registry(
    input_modules: dict[str, PreparedPlugin],
    devices: dict[str, PreparedPlugin],
) -> dict[str, DynamicFieldEntry]:
    """Build the registry from currently loaded plugins.

    Cheap to rebuild — iterates only declared entries, no I/O. Callers
    can either cache the result or rebuild per request; this module
    doesn't keep state.
    """
    registry: dict[str, DynamicFieldEntry] = {}
    for plugin_id, prepared in input_modules.items():
        _collect("input_modules", "input_module", plugin_id, prepared, registry)
    for plugin_id, prepared in devices.items():
        _collect("devices", "device", plugin_id, prepared, registry)
    return registry


async def resolve_value(entry: DynamicFieldEntry) -> Any:
    """Call the plugin's resolver. Returns None and logs on failure so a
    single broken field doesn't sink the whole values blob.
    """
    try:
        return await entry.plugin_instance.resolve_dynamic_field(entry.subpath)
    except KeyError:
        logger.warning(
            "Plugin %s did not resolve dynamic field %r (declared but "
            "resolve_dynamic_field raised KeyError)",
            entry.plugin_id,
            entry.subpath,
        )
        return None
    except Exception as e:  # noqa: BLE001 — defensive: never propagate
        logger.exception(
            "Failed to resolve dynamic field %s for plugin %s: %s",
            entry.subpath,
            entry.plugin_id,
            e,
        )
        return None
