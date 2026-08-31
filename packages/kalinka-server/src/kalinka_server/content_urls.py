"""Where a module's content is addressed.

An input module names an asset it owns; the URL for it is minted here, against
the address the fetcher itself reached us on. The module cannot mint one: it
knows neither which of this server's interfaces a fetcher is on nor whether its
own backend is reachable from there at all.

Kept free of the web framework so the playback path can address content without
depending on how it is served — that lives in :mod:`content_route`.
"""

from urllib.parse import quote

from .netutils import server_base_url

CONTENT_ROUTE = "/content"


def content_url(server_addr: tuple[str, int], module: str, asset_id: str) -> str:
    """Where a fetcher retrieves a ModuleAsset; `server_addr` is the server
    address that fetcher reached us on (RendererRecord.server_addr)."""
    return (
        f"{server_base_url(server_addr)}{CONTENT_ROUTE}"
        f"/{quote(module, safe='')}/{quote(asset_id, safe='')}"
    )
