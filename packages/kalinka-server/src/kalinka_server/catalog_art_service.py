"""Server-generated background art for catalog cards.

``decorate()`` runs inline on every ``/browse`` response: for each imageless
catalog item it fills in the URL of ready art or enqueues generation, never
blocking. A single worker drains the queue — browsing the catalog's first
page, rendering via :mod:`catalog_art_render`, and writing the JPEG under
``<prefix>/var/cache/kalinka/catalog_art/``. File names embed a content
fingerprint, so a card's URL changes only when its content does; the worker
re-checks every ``REFRESH_SECONDS`` but re-renders only on a fingerprint move.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

import httpx
from PIL import Image

from kalinka_plugin_sdk.datamodel import (
    BrowseItem,
    BrowseItemList,
    CoverImage,
    EntityId,
    PreviewContentType,
    PreviewType,
)
from kalinka_plugin_sdk.inputmodule import InputModule

from .catalog_art_render import (
    STYLE_VERSION,
    content_fingerprint,
    encode_jpeg,
    render_catalog_art,
)

logger = logging.getLogger(__name__.split(".")[-1])

ART_URL_PREFIX = "/catalog/art"

#: Re-check a card's inputs this often; a no-op unless the content changed.
REFRESH_SECONDS = 24 * 3600
#: After a failed attempt (module down, remote cache warming) retry sooner.
FAIL_RETRY_SECONDS = 10 * 60
FETCH_LIMIT = 8  # one upstream page per card
MAX_COVERS = 3
MAX_COVER_BYTES = 8 * 1024 * 1024

_FILE_RE = re.compile(r"^[0-9a-f]{16}-[0-9a-f]{8}\.jpg$")


def _item_image_path(item: BrowseItem) -> Optional[str]:
    """Best cover path/URL of a browse item, mirroring the client getter."""
    image: Optional[CoverImage] = None
    if item.album is not None:
        image = item.album.image
    elif item.artist is not None:
        image = item.artist.image
    elif item.playlist is not None:
        image = item.playlist.image
    elif item.catalog is not None:
        image = item.catalog.image
    elif item.track is not None and item.track.album is not None:
        image = item.track.album.image
    if image is None:
        return None
    return image.large or image.small or image.thumbnail


def _has_image(item: BrowseItem) -> bool:
    catalog = item.catalog
    if catalog is None or catalog.image is None:
        return False
    return bool(catalog.image.large or catalog.image.small or catalog.image.thumbnail)


def _textual_hint(item: BrowseItem) -> bool:
    preview = item.catalog.preview_config if item.catalog else None
    if preview is None:
        return False
    return (
        preview.type == PreviewType.TEXT_ONLY
        or preview.content_type == PreviewContentType.CATALOG
    )


class CatalogArtService:
    """Generates, caches and serves composed catalog-card backgrounds."""

    def __init__(
        self,
        cache_dir: str,
        module_resolver: Callable[[EntityId], Optional[InputModule]],
    ) -> None:
        self._dir = Path(cache_dir)
        self._resolver = module_resolver
        self._queue: asyncio.Queue[tuple[str, bool]] = asyncio.Queue(maxsize=128)
        self._pending: set[str] = set()
        self._http: Optional[httpx.AsyncClient] = None
        # catalog id -> {"file": str|None, "fingerprint": str, "next_check_at": float}
        self._entries: dict[str, dict] = {}
        self._load_index()

    # ------------------------------------------------------------------
    # Index persistence
    # ------------------------------------------------------------------

    @property
    def _index_path(self) -> Path:
        return self._dir / "index.json"

    def _load_index(self) -> None:
        try:
            raw = json.loads(self._index_path.read_text())
            self._entries = dict(raw.get("entries", {}))
            if raw.get("style_version") != STYLE_VERSION:
                # New look: re-render every card at the next browse.
                for entry in self._entries.values():
                    entry["next_check_at"] = 0.0
        except FileNotFoundError:
            self._entries = {}
        except (OSError, ValueError) as exc:
            logger.warning("Catalog art index unreadable, starting clean: %s", exc)
            self._entries = {}
        # Drop entries whose file vanished and files no entry references.
        referenced = set()
        for cat_id, entry in list(self._entries.items()):
            name = entry.get("file")
            if name:
                if (self._dir / name).is_file():
                    referenced.add(name)
                else:
                    del self._entries[cat_id]
        try:
            for path in self._dir.glob("*.jpg"):
                if path.name not in referenced:
                    path.unlink(missing_ok=True)
        except OSError:
            pass

    def _save_index(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self._dir), suffix=".tmp")
            with os.fdopen(fd, "w") as handle:
                json.dump(
                    {"style_version": STYLE_VERSION, "entries": self._entries},
                    handle,
                )
            os.replace(tmp, self._index_path)
        except OSError as exc:
            logger.warning("Could not persist catalog art index: %s", exc)

    # ------------------------------------------------------------------
    # Inline decoration (fast path, no I/O)
    # ------------------------------------------------------------------

    def decorate(self, result: BrowseItemList) -> None:
        """Fill in generated art URLs on catalog items that have no image of
        their own; enqueue (re-)generation where needed. Never blocks."""
        for item in result.items:
            if item.catalog is None or not item.can_browse or _has_image(item):
                continue
            cat_id = item.id.to_string if isinstance(item.id, EntityId) else str(item.id)
            entry = self._entries.get(cat_id)
            if entry and entry.get("file"):
                url = f"{ART_URL_PREFIX}/{entry['file']}"
                item.catalog.image = CoverImage(
                    small=url, thumbnail=url, large=url
                )
            if entry is None or time.time() >= entry.get("next_check_at", 0.0):
                self._schedule(cat_id, _textual_hint(item))

    def _schedule(self, cat_id: str, textual: bool) -> None:
        if cat_id in self._pending:
            return
        try:
            self._queue.put_nowait((cat_id, textual))
            self._pending.add(cat_id)
        except asyncio.QueueFull:
            logger.debug("Catalog art queue full; skipping %s", cat_id)

    # ------------------------------------------------------------------
    # Serving
    # ------------------------------------------------------------------

    def art_file(self, file_name: str) -> Optional[Path]:
        """Validated path of a generated file, or None (bad name / missing)."""
        if not _FILE_RE.match(file_name):
            return None
        path = self._dir / file_name
        return path if path.is_file() else None

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Drain the generation queue for the server's lifetime."""
        while True:
            cat_id, textual = await self._queue.get()
            try:
                await self._process(cat_id, textual)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_failure(cat_id)
                logger.warning("Catalog art generation failed for %s: %r", cat_id, exc)
            finally:
                self._pending.discard(cat_id)
                self._queue.task_done()

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _record_failure(self, cat_id: str) -> None:
        entry = self._entries.setdefault(cat_id, {})
        entry["next_check_at"] = time.time() + FAIL_RETRY_SECONDS
        self._save_index()

    async def _process(self, cat_id: str, textual: bool) -> None:
        entity_id = EntityId.from_string(cat_id)
        module = self._resolver(entity_id)
        if module is None:
            self._record_failure(cat_id)
            return

        children = await module.browse(entity_id, offset=0, limit=FETCH_LIMIT)
        items = children.items or []
        if not items:
            # Empty page — often a remote cache still warming; retry soon.
            self._record_failure(cat_id)
            return

        names = [item.name for item in items if item.name]
        catalog_children = sum(1 for item in items if item.catalog is not None)
        textual = textual or catalog_children > len(items) / 2

        covers: list[Image.Image] = []
        cover_bytes: list[bytes] = []
        if not textual:
            seen: set[str] = set()
            for item in items:
                path = _item_image_path(item)
                if not path or path in seen:
                    continue
                seen.add(path)
                blob = await self._fetch_cover(path, item)
                if blob is None:
                    continue
                try:
                    cover = Image.open(io.BytesIO(blob))
                    cover.load()
                except Exception:
                    continue
                covers.append(cover)
                cover_bytes.append(blob)
                if len(covers) >= MAX_COVERS:
                    break

        baked_names = names[:3] if not covers else []
        fingerprint = content_fingerprint(cover_bytes, baked_names, cat_id)

        entry = self._entries.get(cat_id)
        if (
            entry
            and entry.get("fingerprint") == fingerprint
            and entry.get("file")
            and (self._dir / entry["file"]).is_file()
        ):
            entry["next_check_at"] = time.time() + REFRESH_SECONDS
            self._save_index()
            return

        image = await asyncio.to_thread(
            render_catalog_art, covers, baked_names, cat_id
        )
        data = await asyncio.to_thread(encode_jpeg, image)

        file_name = (
            f"{hashlib.sha1(cat_id.encode()).hexdigest()[:16]}-{fingerprint[:8]}.jpg"
        )
        self._dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._dir), suffix=".tmp")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, self._dir / file_name)

        old_file = entry.get("file") if entry else None
        if old_file and old_file != file_name:
            (self._dir / old_file).unlink(missing_ok=True)

        self._entries[cat_id] = {
            "file": file_name,
            "fingerprint": fingerprint,
            "next_check_at": time.time() + REFRESH_SECONDS,
        }
        self._save_index()
        logger.info(
            "Generated catalog art for %s (%s, %d covers, %d names)",
            cat_id,
            file_name,
            len(covers),
            len(baked_names),
        )

    async def _fetch_cover(self, path: str, item: BrowseItem) -> Optional[bytes]:
        """Cover bytes for a child item: local resources straight from disk
        via the owning module, remote URLs over HTTP."""
        if path.startswith("http://") or path.startswith("https://"):
            if self._http is None:
                self._http = httpx.AsyncClient(
                    follow_redirects=True, timeout=httpx.Timeout(10.0)
                )
            try:
                response = await self._http.get(path)
                response.raise_for_status()
                if len(response.content) > MAX_COVER_BYTES:
                    return None
                return response.content
            except httpx.HTTPError as exc:
                logger.debug("Cover fetch failed for %s: %r", path, exc)
                return None

        resource = path.removeprefix("/resource/").removeprefix("resource/")
        item_id = (
            item.id
            if isinstance(item.id, EntityId)
            else EntityId.from_string(str(item.id))
        )
        module = self._resolver(item_id)
        if module is None:
            return None
        try:
            file_path = await module.get_resource_path(resource)
            if not file_path:
                return None
            return await asyncio.to_thread(Path(file_path).read_bytes)
        except Exception as exc:
            logger.debug("Local cover read failed for %s: %r", path, exc)
            return None
