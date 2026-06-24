"""Polite, SSRF-bounded, raw-storing HTTP client shared by all source clients (main-thread code).

- SSRF boundary: HTTPS only + host allowlist (Phase-2 URLs target fixed APIs, never user input;
  user-supplied scraping URLs in Phase 3 get the full private-IP/metadata treatment on top of this).
- Politeness: identifying User-Agent + per-host min-interval throttle + retry/backoff on 429/5xx.
- Replayability + budget: every response is persisted to the RawStore before return, and reads are
  served from it (a re-run of the same seed hits cache instead of re-billing OpenAlex).
- Telemetry: `live_calls` / `cache_hits` drive the budget logging in run.py.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

from backend.repopulation.clients.rawstore import RawStore, cache_key

_RETRYABLE = {429, 500, 502, 503, 504}


class SSRFError(ValueError):
    """Raised when a request target violates the HTTPS/host-allowlist boundary."""


class HttpClient:
    def __init__(
        self,
        raw_store: RawStore,
        allowed_hosts: set[str],
        user_agent: str,
        *,
        min_interval: float = 0.12,
        timeout: float = 30.0,
        max_retries: int = 4,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ) -> None:
        self._raw = raw_store
        self._allowed = set(allowed_hosts)
        self._client = httpx.Client(
            headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=False
        )
        self._min_interval = min_interval
        self._last: dict[str, float] = {}
        self._max_retries = max_retries
        self._sleep = sleep
        self._monotonic = monotonic
        self.live_calls = 0
        self.cache_hits = 0

    def _check_ssrf(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise SSRFError(f"non-HTTPS URL blocked: {url}")
        if parsed.hostname not in self._allowed:
            raise SSRFError(f"host not on allowlist: {parsed.hostname!r}")
        return parsed.hostname

    def _throttle(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            wait = self._min_interval - (self._monotonic() - last)
            if wait > 0:
                self._sleep(wait)
        self._last[host] = self._monotonic()

    def get_json(self, url: str, params: dict | None = None, *, use_cache: bool = True) -> tuple:
        """GET JSON. Returns (body, raw_key). Serves from the RawStore on a cache hit."""
        host = self._check_ssrf(url)
        key = cache_key(url, params)
        if use_cache:
            cached = self._raw.get(key)
            if cached is not None:
                self.cache_hits += 1
                return cached, key

        backoff = 1.0
        last_resp = None
        for attempt in range(self._max_retries + 1):
            self._throttle(host)
            resp = self._client.get(url, params=params)
            self.live_calls += 1
            last_resp = resp
            if resp.status_code in _RETRYABLE and attempt < self._max_retries:
                retry_after = resp.headers.get("Retry-After", "")
                self._sleep(float(retry_after) if retry_after.isdigit() else backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            resp.raise_for_status()
            body = resp.json()
            self._raw.put(
                key, {"url": url, "params": params, "status": resp.status_code, "body": body}
            )
            return body, key

        last_resp.raise_for_status()  # retries exhausted on a retryable status
        raise RuntimeError("unreachable")

    def post_json(self, url: str, json_body: dict, *, headers: dict | None = None,
                  use_cache: bool = True) -> tuple:
        """POST JSON. Returns (body, raw_key). Cached by (url, body) — re-embedding identical
        input is a cache hit (budget)."""
        host = self._check_ssrf(url)
        key = cache_key(url, json_body)
        if use_cache:
            cached = self._raw.get(key)
            if cached is not None:
                self.cache_hits += 1
                return cached, key

        backoff = 1.0
        last_resp = None
        for attempt in range(self._max_retries + 1):
            self._throttle(host)
            resp = self._client.post(url, json=json_body, headers=headers)
            self.live_calls += 1
            last_resp = resp
            if resp.status_code in _RETRYABLE and attempt < self._max_retries:
                retry_after = resp.headers.get("Retry-After", "")
                self._sleep(float(retry_after) if retry_after.isdigit() else backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            resp.raise_for_status()
            body = resp.json()
            self._raw.put(key, {"url": url, "status": resp.status_code, "body": body})
            return body, key

        last_resp.raise_for_status()
        raise RuntimeError("unreachable")

    def close(self) -> None:
        self._client.close()
