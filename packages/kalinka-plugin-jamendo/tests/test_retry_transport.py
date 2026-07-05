"""RetryTransport policy: retry fast transient failures, never timeouts.

A timeout has already consumed the full per-attempt budget — retrying it
turned one 3s failure into 7.5s. Connection-level blips fail in
milliseconds and stay retryable.
"""

import httpx
import pytest

from kalinka_plugin_jamendo.jamendo import RetryTransport


def _transport(monkeypatch, outcomes):
    """RetryTransport whose parent transport yields ``outcomes`` in order
    (an exception instance to raise, or a Response to return)."""
    calls = []

    async def fake_handle(self, request):
        calls.append(request)
        outcome = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", fake_handle
    )
    t = RetryTransport(read_retries=2)
    t.backoff_factor = 0  # keep the test instant
    return t, calls


async def test_read_timeout_fails_fast_without_retry(monkeypatch):
    t, calls = _transport(monkeypatch, [httpx.ReadTimeout("")])

    with pytest.raises(httpx.ReadTimeout):
        await t.handle_async_request(None)

    assert len(calls) == 1  # no second attempt after a burned budget


async def test_connect_error_is_retried_then_succeeds(monkeypatch):
    ok = httpx.Response(200)
    t, calls = _transport(monkeypatch, [httpx.ConnectError("boom"), ok])

    response = await t.handle_async_request(None)

    assert response is ok
    assert len(calls) == 2


async def test_final_5xx_is_returned_not_swallowed(monkeypatch):
    t, calls = _transport(monkeypatch, [httpx.Response(503), httpx.Response(503)])

    response = await t.handle_async_request(None)

    assert response.status_code == 503
    assert len(calls) == 2  # one retry, then the response is handed back
