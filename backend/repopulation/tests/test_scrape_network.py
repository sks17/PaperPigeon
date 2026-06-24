"""Unit tests for the scraper network layer (no real network): LLM client, robots, fetch, discovery."""
from __future__ import annotations

import httpx
import pytest

from backend.repopulation.clients.http import HttpClient
from backend.repopulation.clients.llm import LlmClient, LlmError
from backend.repopulation.clients.rawstore import LocalRawStore
from backend.repopulation.scraping.discovery import discover_lab_urls
from backend.repopulation.scraping.fetch import Fetcher
from backend.repopulation.scraping.robots import RobotsCache


# ── LLM client (httpx.MockTransport) ─────────────────────────────────────────
def _llm(handler, tmp_path):
    http = HttpClient(LocalRawStore(tmp_path), {"openrouter.ai"}, "ua", min_interval=0.0)
    http._client.close()
    http._client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return LlmClient(http, "key"), http


def test_llm_parses_json_content(tmp_path):
    def handler(_req):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"lab_name": "X"}'}}]})
    llm, http = _llm(handler, tmp_path)
    try:
        assert llm.complete_json("sys", "user") == {"lab_name": "X"}
    finally:
        http.close()


def test_llm_bad_shape_raises(tmp_path):
    def handler(_req):
        return httpx.Response(200, json={"unexpected": 1})
    llm, http = _llm(handler, tmp_path)
    try:
        with pytest.raises(LlmError):
            llm.complete_json("sys", "user")
    finally:
        http.close()


# ── robots.txt ───────────────────────────────────────────────────────────────
class _StubHttpText:
    def __init__(self, texts=None):
        self.texts = texts or {}
        self.calls = []

    def get_text(self, url, *, use_cache=True):
        self.calls.append(url)
        if url not in self.texts:
            raise RuntimeError("404")
        return {"body": self.texts[url], "content_hash": "h"}, "k"


def test_robots_disallow_and_delay():
    rc = RobotsCache(
        _StubHttpText({"https://x.edu/robots.txt": "User-agent: *\nDisallow: /private/\nCrawl-delay: 2"}),
        "ua",
    )
    assert rc.can_fetch("https://x.edu/people/faculty")
    assert not rc.can_fetch("https://x.edu/private/secret")
    assert rc.crawl_delay("https://x.edu/") == 2.0


def test_robots_missing_allows_all():
    rc = RobotsCache(_StubHttpText({}), "ua")
    assert rc.can_fetch("https://x.edu/anything")


# ── fetch (SSRF + robots gating) ─────────────────────────────────────────────
def _resolver(ip):
    def r(host, port, **kw):
        return [(2, 1, 6, "", (ip, port))]
    return r


class _StubRobots:
    def __init__(self, allow=True):
        self.allow = allow

    def can_fetch(self, url):
        return self.allow

    def crawl_delay(self, url):
        return None


def test_fetch_blocks_off_domain():
    f = Fetcher(_StubHttpText(), _StubRobots(), {"x.edu"}, resolver=_resolver("93.184.216.34"))
    assert f.fetch("https://evil.com/p") is None and f.skipped


def test_fetch_blocks_private_ip():
    f = Fetcher(_StubHttpText(), _StubRobots(), {"x.edu"}, resolver=_resolver("10.0.0.5"))
    assert f.fetch("https://x.edu/p") is None


def test_fetch_respects_robots():
    f = Fetcher(_StubHttpText(), _StubRobots(allow=False), {"x.edu"}, resolver=_resolver("93.184.216.34"))
    assert f.fetch("https://x.edu/p") is None


def test_fetch_success_returns_record():
    http = _StubHttpText({"https://x.edu/people/": "<html>ok</html>"})
    f = Fetcher(http, _StubRobots(), {"x.edu"}, resolver=_resolver("93.184.216.34"))
    record = f.fetch("https://x.edu/people/")
    assert record is not None and record["body"] == "<html>ok</html>"
    assert http.calls == ["https://x.edu/people/"]


# ── discovery (sanity filtering) ─────────────────────────────────────────────
class _DiscFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.skipped = []

    def fetch(self, url, *, use_cache=True):
        html = self.pages.get(url)
        return {"body": html, "content_hash": "h"} if html is not None else None


def test_discovery_keeps_in_domain_lab_paths_only():
    home = "https://x.edu/"
    html = (
        '<a href="/people/faculty">f</a> <a href="/labs/vision">v</a> '
        '<a href="https://evil.com/labs/x">off</a> <a href="/about">about</a>'
    )
    urls = discover_lab_urls(home, _DiscFetcher({home: html}), allowed_domains={"x.edu"}, max_pages=10)
    assert "https://x.edu/people/faculty" in urls
    assert "https://x.edu/labs/vision" in urls
    assert all("evil.com" not in u for u in urls)        # off-domain dropped
    assert all(not u.endswith("/about") for u in urls)   # non-faculty path dropped


def test_discovery_caps_results():
    home = "https://x.edu/"
    html = " ".join(f'<a href="/labs/l{i}">l</a>' for i in range(20))
    urls = discover_lab_urls(home, _DiscFetcher({home: html}), allowed_domains={"x.edu"}, max_pages=5)
    assert len(urls) == 5
