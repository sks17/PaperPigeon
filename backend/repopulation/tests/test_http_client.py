"""Unit tests for the polite, SSRF-bounded, raw-storing HttpClient  [Cursor task P2-T07].

Exercises the three load-bearing behaviors of `clients/http.py` with NO real network:
  - SSRF boundary: non-HTTPS and non-allowlisted hosts are refused before any request.
  - Politeness: a retryable 5xx is retried after a backoff sleep, then succeeds.
  - Replayability/budget: the response is written through to the RawStore on the first (live) call,
    and an identical second call is served from the store (cache_hits++, live_calls unchanged).

The MockTransport is injected via the underlying httpx client; a fake clock makes sleep/monotonic
deterministic and asserts that the retry path actually sleeps.
"""
from __future__ import annotations

import httpx
import pytest

from backend.repopulation.clients.http import HttpClient, SSRFError
from backend.repopulation.clients.rawstore import LocalRawStore, cache_key

ALLOWED_HOST = "api.example.com"
URL = f"https://{ALLOWED_HOST}/works"


class FakeClock:
    """Deterministic stand-in for time.sleep / time.monotonic (no wall-clock in tests)."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


def _make_client(
    handler,
    tmp_path,
    clock: FakeClock,
    *,
    allowed_hosts=(ALLOWED_HOST,),
    max_retries: int = 4,
) -> HttpClient:
    """HttpClient whose underlying httpx.Client is backed by a MockTransport (no sockets)."""
    store = LocalRawStore(tmp_path)
    client = HttpClient(
        store,
        set(allowed_hosts),
        user_agent="paper-pigeon-tests/1.0",
        min_interval=0.0,
        max_retries=max_retries,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    # Inject the MockTransport via the underlying client (constructor builds its own httpx.Client).
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return client


def test_ssrf_blocks_non_https_url(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("transport must not be reached for an SSRF-blocked URL")

    client = _make_client(handler, tmp_path, FakeClock())
    try:
        with pytest.raises(SSRFError):
            client.get_json(f"http://{ALLOWED_HOST}/works")
        # Blocked before any network activity.
        assert client.live_calls == 0
    finally:
        client.close()


def test_ssrf_blocks_non_allowlisted_host(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("transport must not be reached for a non-allowlisted host")

    client = _make_client(handler, tmp_path, FakeClock())
    try:
        with pytest.raises(SSRFError):
            client.get_json("https://evil.example.org/works")
        assert client.live_calls == 0
    finally:
        client.close()


def test_retry_on_503_then_success_sleeps(tmp_path) -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    clock = FakeClock()
    client = _make_client(handler, tmp_path, clock)
    try:
        body, key = client.get_json(URL)

        assert body == {"ok": True}
        assert attempts["n"] == 2  # one failed + one successful request
        assert client.live_calls == 2
        assert client.cache_hits == 0
        # The retry path slept on the backoff (default 1.0s for the first retry, no Retry-After).
        assert clock.sleeps == [1.0]
        # Success was written through to the raw store.
        assert client._raw.get(key) == {"ok": True}
    finally:
        client.close()


def test_write_through_then_second_call_is_cache_hit(tmp_path) -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(200, json={"hello": "world", "attempt": attempts["n"]})

    clock = FakeClock()
    client = _make_client(handler, tmp_path, clock)
    try:
        first_body, key = client.get_json(URL)

        assert first_body == {"hello": "world", "attempt": 1}
        assert client.live_calls == 1
        assert client.cache_hits == 0
        # Write-through: the response is persisted under the GET cache key.
        assert key == cache_key(URL, {})
        assert client._raw.get(key) == first_body

        second_body, second_key = client.get_json(URL)

        # Served from the store: transport not reached again, telemetry reflects a cache hit.
        assert attempts["n"] == 1
        assert second_key == key
        assert second_body == first_body
        assert client.live_calls == 1  # unchanged
        assert client.cache_hits == 1
    finally:
        client.close()


def test_api_key_param_is_redacted_from_the_raw_store(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    clock = FakeClock()
    client = _make_client(handler, tmp_path, clock)
    try:
        _, key = client.get_json(URL, params={"api_key": "s3cret", "filter": "x"})

        # The cache key is computed over non-secret params only...
        assert key == cache_key(URL, {"filter": "x"})
        # ...and the secret is never written to disk.
        stored = (client._raw.root / f"{key}.json").read_text(encoding="utf-8")
        assert "s3cret" not in stored
        assert "api_key" not in stored
    finally:
        client.close()
