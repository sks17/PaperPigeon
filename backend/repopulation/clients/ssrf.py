"""SSRF validation for the scraper (main-thread security code).

Phase 2's HttpClient._check_ssrf is an allowlist of FIXED API hosts. Phase 3 fetches discovered/
user-influenced institution URLs, so it needs the stronger guard the threat model requires
(04-infrastructure-security-and-roadmap.md → Security): HTTPS only, same-registrable-domain
allowlist, AND resolve the host and reject private / loopback / link-local / metadata IP ranges
(incl. 169.254.169.254) — defeating DNS-rebinding to internal addresses. The resolver is injectable
so tests are deterministic without real DNS.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class SsrfError(ValueError):
    """Raised when a scrape target violates the HTTPS / domain / IP boundary."""


def registrable_domain_match(host: str, allowed_domains: set[str]) -> bool:
    """True if host equals or is a subdomain of any allowed domain (e.g. cs.washington.edu ⊆ washington.edu)."""
    host = host.lower().rstrip(".")
    for domain in allowed_domains:
        domain = domain.lower().rstrip(".")
        if host == domain or host.endswith("." + domain):
            return True
    return False


def is_blocked_ip(ip_str: str) -> bool:
    """Block anything not a normal public address: private, loopback, link-local (incl. cloud
    metadata 169.254.169.254), reserved, multicast, unspecified."""
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_scrape_url(url: str, allowed_domains: set[str], *, resolver=socket.getaddrinfo) -> str:
    """Return the host if `url` is safe to fetch; raise SsrfError otherwise."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SsrfError(f"non-HTTPS URL blocked: {url}")
    host = parsed.hostname
    if not host:
        raise SsrfError(f"no host in URL: {url}")
    if not registrable_domain_match(host, allowed_domains):
        raise SsrfError(f"host {host!r} not under an allowed domain {sorted(allowed_domains)}")

    try:
        infos = resolver(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except Exception as exc:  # DNS failure → refuse rather than fetch blindly
        raise SsrfError(f"DNS resolution failed for {host!r}: {exc}") from exc

    for info in infos:
        ip_str = info[4][0]
        if is_blocked_ip(ip_str):
            raise SsrfError(f"host {host!r} resolved to non-public IP {ip_str}")
    return host
