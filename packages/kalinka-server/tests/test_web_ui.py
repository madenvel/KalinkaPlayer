"""WebUiStaticFiles: install-page fallback vs. serving an installed bundle."""

from __future__ import annotations

import pytest
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, HTMLResponse

from kalinka_server.web_ui import WebUiStaticFiles


def _scope(method: str, path: str) -> dict:
    return {"type": "http", "method": method, "headers": [], "path": path}


async def _get(directory, *, sub_path: str = "", url_path: str = "/", method: str = "GET"):
    sf = WebUiStaticFiles(directory=str(directory), html=True, check_dir=False)
    return await sf.get_response(sub_path, _scope(method, url_path))


async def test_absent_bundle_serves_install_page(tmp_path):
    resp = await _get(tmp_path / "missing")
    assert isinstance(resp, HTMLResponse)
    assert resp.status_code == 200
    assert b"Web player not installed" in resp.body


async def test_absent_bundle_head_has_empty_body(tmp_path):
    resp = await _get(tmp_path / "missing", method="HEAD")
    assert not isinstance(resp, HTMLResponse)
    assert resp.status_code == 200
    assert resp.body == b""


async def test_installed_bundle_serves_index(tmp_path):
    (tmp_path / "index.html").write_text("<h1>real bundle</h1>")
    resp = await _get(tmp_path)
    # A real file response — the install page must not shadow the bundle.
    assert isinstance(resp, FileResponse)
    assert resp.status_code == 200


async def test_installed_bundle_missing_asset_is_404(tmp_path):
    (tmp_path / "index.html").write_text("<h1>real bundle</h1>")
    with pytest.raises(HTTPException) as exc_info:
        await _get(tmp_path, sub_path="missing.js", url_path="/missing.js")
    assert exc_info.value.status_code == 404
