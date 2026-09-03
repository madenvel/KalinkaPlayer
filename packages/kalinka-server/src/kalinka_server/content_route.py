"""Serving a module's content on its behalf.

Every content link points at this server rather than at the module's own
backend, so a fetcher only ever has to reach the address it is already talking
to. :mod:`content_urls` mints those links.

Ranged requests are answered by FileResponse: a renderer seeks by asking for
byte ranges, and reads the stream size out of ``Content-Range``.
"""

from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from kalinka_plugin_sdk.inputmodule import InputModule, SourceUnavailableError

from .content_urls import CONTENT_ROUTE


def register_content_route(
    app: FastAPI, resolve_module: Callable[[str], InputModule]
) -> None:
    """Mount the content endpoint on `app`.

    ``resolve_module`` maps a module name to the input module, raising for one
    that is unknown or disabled.
    """

    # HEAD as well as GET: FastAPI does not answer it off a GET route the way a
    # mount does, and a client may ask for the size and type alone.
    # `:path` because an asset id is opaque and may hold a slash: the %2F the
    # link carries is decoded before the route is matched, and a single-segment
    # converter would not match what the module actually minted.
    @app.api_route(
        f"{CONTENT_ROUTE}/{{module_name}}/{{asset_id:path}}", methods=["GET", "HEAD"]
    )
    async def get_content(module_name: str, asset_id: str):
        # An unmounted share answers 503, not 404: renderers retry 5xx but
        # treat 4xx as fatal.
        try:
            info = await resolve_module(module_name).get_content_info(asset_id)
        except SourceUnavailableError as e:
            raise HTTPException(
                status_code=503, detail=str(e), headers={"Retry-After": "2"}
            )
        # A file the module will not name, or that went away since it did, is
        # absent rather than a server fault — FileResponse would raise on the
        # missing stat.
        if info is None or not info.local_path or not Path(info.local_path).is_file():
            raise HTTPException(status_code=404, detail="Content not found")

        return FileResponse(
            info.local_path,
            media_type=info.mime_type,
            headers={"Accept-Ranges": "bytes"},
        )
