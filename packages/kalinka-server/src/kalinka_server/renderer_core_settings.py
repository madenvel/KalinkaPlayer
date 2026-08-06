"""What the Core keeps about a renderer, shown on the renderer's own page.

A renderer knows its driver, its card and how it applies volume. What its
output is wired into — an amp or a receiver with a volume of its own — it
cannot see, so the Core holds that mapping and answers for it. It rides the
renderer's settings payload, and is written by path like everything else there,
so the page reads as one thing.

Mapping a renderer to a module moves whose volume and power clients drive: the
module takes over while that renderer is the one playing, and switching
renderers switches it with them.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Awaitable, Callable, Mapping, Optional

from .renderer_prefs import RendererPreferences

logger = logging.getLogger(__name__.split(".")[-1])

SECTION_PATH = "core.output"
DEVICE_MODULE_PATH = "core.output.device_module"

# The absent mapping: the renderer controls itself.
RENDERER_ITSELF = ""
RENDERER_ITSELF_LABEL = "This renderer"

# What a module is offered as, when it has no title of its own.
Candidates = Callable[[], list[tuple[str, str]]]


class CoreRendererSettings:
    def __init__(
        self,
        prefs: RendererPreferences,
        candidates: Candidates,
        on_changed: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self._prefs = prefs
        # (module id, label) of every module a renderer can be mapped to.
        self._candidates = candidates
        self._on_changed = on_changed

    def owns(self, path: str) -> bool:
        return path == DEVICE_MODULE_PATH

    def section(self, renderer_id: str) -> dict:
        candidates = self._candidates()
        mapped = self._prefs.volume_control(renderer_id) or RENDERER_ITSELF
        options = [
            {
                "value": RENDERER_ITSELF,
                "label": RENDERER_ITSELF_LABEL,
                "description": "Volume is applied by the renderer itself.",
            }
        ] + [
            {"value": module, "label": label, "description": ""}
            for module, label in candidates
        ]
        if mapped and all(module != mapped for module, _ in candidates):
            # Mapped to something not available right now; it must stay
            # selectable or the page could not show what is set.
            options.append(
                {
                    "value": mapped,
                    "label": f"{mapped} (unavailable)",
                    "description": "Configured, but the module is not loaded.",
                }
            )
        # Nothing to choose between: shown as a value rather than as a
        # chosen option, so it has to carry the label itself.
        read_only = not candidates and not mapped
        return {
            "path": SECTION_PATH,
            "title": "Connected device",
            "description": "",
            "fields": [
                {
                    "path": DEVICE_MODULE_PATH,
                    "title": "Volume and power",
                    "description": (
                        "The amplifier or receiver this renderer plays into. "
                        "Its volume and power replace the renderer's own "
                        "wherever this renderer is playing, and the renderer "
                        "is left at full scale from the next track on so the "
                        "level is set once, downstream."
                    ),
                    "type": "enum",
                    "value": RENDERER_ITSELF_LABEL if read_only else mapped,
                    "default": RENDERER_ITSELF,
                    "options": options,
                    "apply": "instant",
                    "read_only": read_only,
                }
            ],
        }

    def merge_into(self, snapshot: dict, renderer_id: str) -> dict:
        """The renderer's payload with the Core's section on the end."""
        snapshot.setdefault("sections", []).append(self.section(renderer_id))
        snapshot["config_version"] = (
            f"{snapshot.get('config_version', '')}.{self.fingerprint()}"
        )
        return snapshot

    def split(self, changes: Mapping[str, Any]) -> tuple[dict, dict]:
        """The changes that are ours, and the ones for the renderer."""
        ours = {path: value for path, value in changes.items() if self.owns(path)}
        theirs = {
            path: value for path, value in changes.items() if path not in ours
        }
        return ours, theirs

    def fingerprint(self) -> str:
        """Folds into the renderer's config_version: the options change as
        modules come and go, and a page holding the old shape must know."""
        blob = "\x1f".join(module for module, _ in self._candidates())
        return hashlib.blake2s(blob.encode(), digest_size=4).hexdigest()

    async def apply(
        self, renderer_id: str, changes: Mapping[str, Any]
    ) -> list[dict]:
        outcomes = []
        for path, value in changes.items():
            outcomes.append(await self._apply_one(renderer_id, path, str(value)))
        return outcomes

    async def _apply_one(self, renderer_id: str, path: str, value: str) -> dict:
        current = self._prefs.volume_control(renderer_id) or RENDERER_ITSELF
        if not self.owns(path):
            return {
                "path": path,
                "applied": False,
                "value": current,
                "error": "no such setting",
            }
        if value != RENDERER_ITSELF and all(
            module != value for module, _ in self._candidates()
        ):
            return {
                "path": path,
                "applied": False,
                "value": current,
                "error": f"'{value}' is not an enabled device module with volume control",
            }
        self._prefs.set_volume_control(
            renderer_id, value if value != RENDERER_ITSELF else None
        )
        logger.info(
            "Renderer %s volume control: %s",
            renderer_id,
            value or "renderer itself",
        )
        if self._on_changed is not None:
            await self._on_changed()
        return {"path": path, "applied": True, "value": value, "error": ""}
