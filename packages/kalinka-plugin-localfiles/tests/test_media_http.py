"""The media HTTP server: id-addressed serving (no paths in URLs), the
renderer's exact request pattern (curl with ``Range: bytes=<offset>-``,
stream size read from Content-Range), and the access boundary shared with
the link retriever."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
import requests

from kalinka_plugin_localfiles.media_http import MediaHttpServer, _parse_range

AUDIO_BYTES = b"RIFF-not-really-audio-but-bytes-enough-for-http"


class FakeDb:
    def __init__(self, tracks: dict):
        self.tracks = tracks

    def get_track_by_id(self, track_id):
        return self.tracks.get(track_id)


@pytest.fixture
def library(tmp_path):
    track = tmp_path / "music" / "song.flac"
    track.parent.mkdir()
    track.write_bytes(AUDIO_BYTES)
    outside = tmp_path / "elsewhere" / "secret.flac"
    outside.parent.mkdir()
    outside.write_bytes(b"outside the roots")
    db = FakeDb(
        {
            "1": {"id": "1", "file_path": str(track)},
            "2": {"id": "2", "file_path": str(outside)},
            "3": {"id": "3", "file_path": str(tmp_path / "music" / "gone.flac")},
        }
    )
    return db, [str(tmp_path / "music")]


@pytest_asyncio.fixture
async def server(library):
    db, roots = library
    srv = MediaHttpServer(db, roots)
    await srv.start()
    yield srv
    await srv.stop()


async def _get(url: str, **kwargs) -> requests.Response:
    return await asyncio.to_thread(requests.get, url, timeout=5, **kwargs)


@pytest.mark.asyncio
async def test_serves_track_by_id(server):
    response = await _get(f"http://127.0.0.1:{server.port}/audio/1")
    assert response.status_code == 200
    assert response.content == AUDIO_BYTES
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(len(AUDIO_BYTES))
    assert response.headers["content-type"] == "audio/flac"


@pytest.mark.asyncio
async def test_renderer_first_request_learns_size(server):
    # curl sends Range: bytes=0- on the very first request; the renderer reads
    # the stream size from the total after "/" in Content-Range.
    response = await _get(
        f"http://127.0.0.1:{server.port}/audio/1",
        headers={"Range": "bytes=0-"},
    )
    assert response.status_code == 206
    assert response.content == AUDIO_BYTES
    assert response.headers["content-range"] == f"bytes 0-{len(AUDIO_BYTES) - 1}/{len(AUDIO_BYTES)}"


@pytest.mark.asyncio
async def test_seek_range(server):
    response = await _get(
        f"http://127.0.0.1:{server.port}/audio/1",
        headers={"Range": "bytes=5-8"},
    )
    assert response.status_code == 206
    assert response.content == AUDIO_BYTES[5:9]
    assert response.headers["content-range"] == f"bytes 5-8/{len(AUDIO_BYTES)}"


@pytest.mark.asyncio
async def test_range_past_eof_is_416(server):
    response = await _get(
        f"http://127.0.0.1:{server.port}/audio/1",
        headers={"Range": f"bytes={len(AUDIO_BYTES)}-"},
    )
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(AUDIO_BYTES)}"


@pytest.mark.asyncio
async def test_keep_alive_serves_sequential_requests(server):
    def fetch_twice():
        with requests.Session() as session:
            first = session.get(
                f"http://127.0.0.1:{server.port}/audio/1",
                headers={"Range": "bytes=0-3"},
                timeout=5,
            )
            second = session.get(
                f"http://127.0.0.1:{server.port}/audio/1",
                headers={"Range": "bytes=4-"},
                timeout=5,
            )
            return first, second

    first, second = await asyncio.to_thread(fetch_twice)
    assert first.content == AUDIO_BYTES[:4]
    assert second.content == AUDIO_BYTES[4:]


@pytest.mark.asyncio
async def test_unknown_gone_and_outside_root_are_404(server):
    for track_id in ("nope", "3", "2"):
        response = await _get(f"http://127.0.0.1:{server.port}/audio/{track_id}")
        assert response.status_code == 404, track_id


@pytest.mark.asyncio
async def test_url_never_contains_a_path(server):
    url = server.url_for("42")
    assert url.startswith("http://")
    assert url.endswith(f":{server.port}/audio/42")
    assert server.port != 0


def test_parse_range_edge_cases():
    assert _parse_range("bytes=0-", 10) == ("range", 0, 10)
    assert _parse_range("bytes=3-5", 10) == ("range", 3, 3)
    assert _parse_range("bytes=3-999", 10) == ("range", 3, 7)
    assert _parse_range("bytes=-4", 10) == ("range", 6, 4)
    assert _parse_range("bytes=10-", 10) == ("unsatisfiable", 0, 0)
    assert _parse_range("bytes=0-", 0) == ("unsatisfiable", 0, 0)
    assert _parse_range("bytes=5-3", 10) == ("full", 0, 10)
    assert _parse_range("bytes=a-b", 10) == ("full", 0, 10)
    assert _parse_range("bytes=0-3,5-7", 10) == ("full", 0, 10)
    assert _parse_range("chunks=0-", 10) == ("full", 0, 10)
