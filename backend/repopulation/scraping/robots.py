"""robots.txt compliance (main-thread). Fetches + caches per-host robots.txt and answers can_fetch /
crawl_delay. A missing/unreadable robots.txt is treated as 'allow' (standard). Uses the polite
HttpClient (raw-cached) so robots.txt is fetched at most once per host per cache window.
"""
from __future__ import annotations

import urllib.robotparser
from urllib.parse import urlparse

from backend.repopulation.clients.http import HttpClient


class RobotsCache:
    def __init__(self, http: HttpClient, user_agent: str) -> None:
        self._http = http
        self._ua = user_agent
        self._parsers: dict[str, urllib.robotparser.RobotFileParser] = {}

    def _base(self, url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    def _parser(self, url: str) -> urllib.robotparser.RobotFileParser:
        base = self._base(url)
        if base in self._parsers:
            return self._parsers[base]
        rp = urllib.robotparser.RobotFileParser()
        try:
            record, _ = self._http.get_text(f"{base}/robots.txt")
            rp.parse((record.get("body") or "").splitlines())
        except Exception:
            rp.parse([])  # no robots.txt → allow all
        self._parsers[base] = rp
        return rp

    def can_fetch(self, url: str) -> bool:
        return self._parser(url).can_fetch(self._ua, url)

    def crawl_delay(self, url: str) -> float | None:
        delay = self._parser(url).crawl_delay(self._ua)
        return float(delay) if delay is not None else None
