"""Lab-page discovery (main-thread). Auto-discovers candidate faculty/lab/people pages from the
institution domain (homepage + sitemap.xml link extraction) and SANITY-CHECKS each candidate:
same registrable domain (+ the Fetcher's SSRF/robots gate), https, a faculty/lab-ish path, deduped,
capped. Never executes JS or follows off-domain links. Returns a bounded candidate URL list.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from backend.repopulation.clients.ssrf import registrable_domain_match
from backend.repopulation.scraping.fetch import Fetcher

# Heuristic: paths that look like faculty/lab/people/research/group directories or pages.
PATH_RE = re.compile(r"/(people|faculty|research|labs?|groups?|directory|members|~)", re.IGNORECASE)
_HREF_RE = re.compile(r'href=["\']([^"\'#]+)', re.IGNORECASE)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)", re.IGNORECASE)


def discover_lab_urls(
    homepage_url: str,
    fetcher: Fetcher,
    *,
    allowed_domains: set[str],
    max_pages: int = 40,
    extra_seeds: tuple[str, ...] = (),
) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        url = raw.split("#")[0].strip()
        if not url or url in seen:
            return
        seen.add(url)
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return
        if not registrable_domain_match(parsed.hostname or "", allowed_domains):
            return
        if not PATH_RE.search(parsed.path or ""):
            return
        candidates.append(url)

    seeds = [homepage_url, urljoin(homepage_url, "/sitemap.xml"), *extra_seeds]
    for seed in seeds:
        record = fetcher.fetch(seed)
        if record is None:
            continue
        body = record.get("body") or ""
        for href in _HREF_RE.findall(body):
            add(urljoin(seed, href))
        for loc in _LOC_RE.findall(body):
            add(urljoin(seed, loc))
        if len(candidates) >= max_pages:
            break

    return candidates[:max_pages]
