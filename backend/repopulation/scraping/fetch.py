"""Polite, SSRF-bounded page fetch (main-thread). Every fetch is gated by validate_scrape_url
(https + domain allowlist + private-IP/metadata block) AND robots.txt before the HTTP request.
Static-first; headless escalation is a stubbed hook (no Playwright-Python yet). Returns the raw-store
record (body + content_hash + from_cache) or None when the URL is skipped (SSRF/robots/error).
"""
from __future__ import annotations

import socket

from backend.repopulation.clients.http import HttpClient
from backend.repopulation.clients.ssrf import SsrfError, validate_scrape_url
from backend.repopulation.scraping.robots import RobotsCache


class Fetcher:
    def __init__(
        self,
        http: HttpClient,
        robots: RobotsCache,
        allowed_domains: set[str],
        *,
        resolver=socket.getaddrinfo,
    ) -> None:
        self._http = http
        self._robots = robots
        self._allowed = set(allowed_domains)
        self._resolver = resolver
        self.skipped: list[tuple[str, str]] = []  # (url, reason) — for reporting

    def fetch(self, url: str, *, use_cache: bool = True) -> dict | None:
        try:
            validate_scrape_url(url, self._allowed, resolver=self._resolver)
        except SsrfError as exc:
            self.skipped.append((url, f"ssrf:{exc}"))
            return None
        if not self._robots.can_fetch(url):
            self.skipped.append((url, "robots-disallow"))
            return None
        try:
            record, _ = self._http.get_text(url, use_cache=use_cache)
        except Exception as exc:  # 4xx/5xx/network — skip this page, don't crash the run
            self.skipped.append((url, f"fetch-error:{type(exc).__name__}"))
            return None
        return record

    def headless_fetch(self, url: str) -> dict:
        raise NotImplementedError(
            "headless escalation not wired (static-only this phase); JS-rendered pages are skipped"
        )
