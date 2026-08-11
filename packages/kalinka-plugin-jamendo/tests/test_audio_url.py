"""Playback URLs must carry a real audio content type.

Without ``action=stream``, /tracks/file/ redirects to Jamendo's download URL,
whose bytes are audio but whose Content-Type is text/html — an HTML <audio>
element picks its decoder from that header and refuses to play (Firefox;
Chrome sniffs the container and gets away with it). With it, the same endpoint
redirects to the tokenised streaming URL, correctly typed.
"""

import httpx
import pytest

from kalinka_plugin_jamendo.jamendo import JamendoClient

STREAM_URL = (
    "https://prod-1.storage.jamendo.com/?trackid=1567448&format=flac&from=tok%3D%3D"
)


def _client(response):
    """Client whose transport answers every request with ``response``,
    recording the requests it saw in ``client.requests``."""
    client = JamendoClient("client-id")
    client.requests = []

    def handler(request):
        client.requests.append(request)
        return response

    client.session = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def _redirect(location=STREAM_URL):
    return httpx.Response(302, headers={"location": location})


async def test_stream_action_is_requested():
    client = _client(_redirect())

    await client.resolve_audio_url("1567448", "flac")

    params = client.requests[0].url.params
    assert params["action"] == "stream"
    assert params["id"] == "1567448"
    assert params["audioformat"] == "flac"


@pytest.mark.parametrize("audioformat", ["mp31", "mp32", "ogg", "flac"])
async def test_every_format_asks_for_the_stream(audioformat):
    client = _client(_redirect())

    await client.resolve_audio_url("1567448", audioformat)

    assert client.requests[0].url.params["action"] == "stream"


async def test_redirect_target_is_handed_back_verbatim():
    client = _client(_redirect())

    assert await client.resolve_audio_url("1567448", "flac") == STREAM_URL


async def test_direct_body_response_returns_the_request_url():
    client = _client(httpx.Response(200))

    url = await client.resolve_audio_url("1567448", "flac")

    assert url.startswith("https://api.jamendo.com/v3.0/tracks/file/")
    assert "id=1567448" in url


async def test_download_disallowed_track_resolves_to_nothing():
    # Jamendo 404s here when audiodownload_allowed is false, stream intent or
    # not. An empty URL makes the caller raise rather than stream "".
    client = _client(httpx.Response(404))

    assert await client.resolve_audio_url("1314412", "flac") == ""


async def test_transport_failure_resolves_to_nothing():
    client = JamendoClient("client-id")

    def boom(request):
        raise httpx.ConnectError("no route")

    client.session = httpx.AsyncClient(transport=httpx.MockTransport(boom))

    assert await client.resolve_audio_url("1567448", "flac") == ""
