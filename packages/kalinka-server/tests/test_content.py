"""The content endpoint and the URLs pointing at it.

The renderer is the demanding client here: it fetches in bounded ranges, reads
the stream size out of ``Content-Range`` on the first of them, and refuses to
seek at all unless ``Accept-Ranges`` says it may. These are the guarantees the
old in-plugin media server made and this endpoint inherits.
"""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from kalinka_plugin_sdk.inputmodule import ContentInfo, SourceUnavailableError

from kalinka_server.content_route import register_content_route
from kalinka_server.content_urls import CONTENT_ROUTE, content_url

AUDIO = bytes(range(256)) * 8  # 2048 bytes


class _FakeModule:
    def __init__(self, assets: dict[str, ContentInfo]):
        self._assets = assets

    async def get_content_info(self, asset_id):
        if asset_id == "offline":
            raise SourceUnavailableError("Music folder /mnt/nas is not available")
        return self._assets.get(asset_id)


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "song.flac"
    path.write_bytes(AUDIO)

    modules = {
        "localfiles": _FakeModule(
            {
                "track_1": ContentInfo(
                    mime_type="audio/flac", local_path=str(path), cacheable=True
                ),
                # A module may know the asset and still refuse to serve it.
                "unservable": ContentInfo(mime_type="audio/flac"),
                # Asset ids are the module's to mint and need not be one path
                # segment; this one survives only if the route allows a slash.
                "disc 1/track 1": ContentInfo(
                    mime_type="audio/flac", local_path=str(path), cacheable=True
                ),
            }
        )
    }

    def resolve_module(name):
        if name not in modules:
            raise HTTPException(status_code=404, detail="Input module not found")
        return modules[name]

    app = FastAPI()
    register_content_route(app, resolve_module)
    return TestClient(app)


def _url(module="localfiles", asset="track_1"):
    return f"{CONTENT_ROUTE}/{module}/{asset}"


def test_serves_the_whole_asset(client):
    r = client.get(_url())
    assert r.status_code == 200
    assert r.content == AUDIO
    assert r.headers["content-type"] == "audio/flac"


def test_advertises_range_support(client):
    """Without this the renderer refuses every seek."""
    assert client.get(_url()).headers["accept-ranges"] == "bytes"


def test_bounded_range_reports_the_total_size(client):
    """The renderer's first request is a bounded range, and it learns the
    stream size from nothing but this header."""
    r = client.get(_url(), headers={"Range": "bytes=0-511"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-511/{len(AUDIO)}"
    assert r.content == AUDIO[:512]


def test_open_ended_range_serves_to_the_end(client):
    r = client.get(_url(), headers={"Range": "bytes=2000-"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 2000-2047/{len(AUDIO)}"
    assert r.content == AUDIO[2000:]


def test_suffix_range_serves_the_tail(client):
    r = client.get(_url(), headers={"Range": "bytes=-48"})
    assert r.status_code == 206
    assert r.content == AUDIO[-48:]


def test_unsatisfiable_range_is_refused(client):
    r = client.get(_url(), headers={"Range": f"bytes={len(AUDIO) + 10}-"})
    assert r.status_code == 416


def test_head_answers_without_a_body(client):
    r = client.head(_url())
    assert r.status_code == 200
    assert r.headers["content-length"] == str(len(AUDIO))
    assert r.content == b""


def test_unknown_asset_is_absent(client):
    assert client.get(_url(asset="no_such_track")).status_code == 404


def test_asset_the_module_will_not_serve_is_absent(client):
    """A module reporting no servable file is a 404, never a 500."""
    assert client.get(_url(asset="unservable")).status_code == 404


def test_unknown_module_is_absent(client):
    assert client.get(_url(module="no_such_module")).status_code == 404


def test_transiently_unreachable_storage_is_a_503(client):
    """An unmounted share must not read as a missing file: renderers retry
    5xx but abort on 4xx, so 503 is what keeps the stream alive."""
    r = client.get(_url(asset="offline"))
    assert r.status_code == 503
    assert r.headers["retry-after"] == "2"


def test_a_file_that_went_away_is_absent(client, tmp_path):
    """Between the module's answer and the read — a 404, not a 500."""
    (tmp_path / "song.flac").unlink()
    assert client.get(_url()).status_code == 404


def test_a_hung_mount_does_not_pin_the_request(client, monkeypatch):
    """The last existence check before FileResponse is bounded too: a mount
    that hangs after the module answered reads as transient, and does not
    hold the event loop while it does."""
    import kalinka_server.content_route as content_route

    class _HungPath(Path):
        def is_file(self):
            time.sleep(5)
            return True

    monkeypatch.setattr(content_route, "_STAT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(content_route, "Path", _HungPath)

    r = client.get(_url())
    assert r.status_code == 503
    assert r.headers["retry-after"] == "2"


def test_content_url_is_built_on_the_address_the_fetcher_reached():
    assert (
        content_url(("192.168.1.10", 8000), "localfiles", "track_1")
        == "http://192.168.1.10:8000/content/localfiles/track_1"
    )


def test_content_url_brackets_an_ipv6_host():
    assert content_url(("fe80::1", 8000), "localfiles", "t1").startswith(
        "http://[fe80::1]:8000/"
    )


def test_a_minted_link_reaches_the_asset_it_names(client):
    """The link is the contract between content_url and this route: an id with
    a slash in it is escaped on the way out and must survive the round trip,
    which a single-segment route silently fails."""
    url = content_url(("testserver", 80), "localfiles", "disc 1/track 1")

    r = client.get(url.removeprefix("http://testserver:80"))

    assert r.status_code == 200
    assert r.content == AUDIO


def test_content_url_escapes_the_asset_id():
    """Ids are opaque — a module may mint one holding a slash or a space."""
    url = content_url(("10.0.0.1", 8000), "localfiles", "a b/c")
    assert url == "http://10.0.0.1:8000/content/localfiles/a%20b%2Fc"


def test_a_disabled_module_serves_no_content(monkeypatch):
    """A module the user has switched off must read as absent, not as a fault:
    `input_module` alone answers 500 once the interface has been torn down."""
    from kalinka_server import server

    prepared = {"localfiles": SimpleNamespace(interface=None)}
    monkeypatch.setattr(
        server.modules,
        "prepared_input_modules",
        prepared,
        raising=False,
    )
    monkeypatch.setattr(server.modules, "enabled_input_modules", set(), raising=False)

    with pytest.raises(HTTPException) as raised:
        server.enabled_input_module("localfiles")

    assert raised.value.status_code == 404
