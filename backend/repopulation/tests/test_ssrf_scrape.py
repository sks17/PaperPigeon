from __future__ import annotations

import socket

import pytest

from backend.repopulation.clients.ssrf import SsrfError, validate_scrape_url


ALLOWED_DOMAINS = {"washington.edu"}


def _resolver_for(ip_by_host: dict[str, str]):
    def resolver(host: str, port: int, *, proto: int = 0):
        ip = ip_by_host[host]
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, proto, "", (ip, port))]

    return resolver


def test_validate_scrape_url_blocks_non_https_before_dns() -> None:
    def resolver(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("non-HTTPS URLs must be rejected before DNS resolution")

    with pytest.raises(SsrfError, match="non-HTTPS"):
        validate_scrape_url(
            "http://cs.washington.edu/research",
            ALLOWED_DOMAINS,
            resolver=resolver,
        )


def test_validate_scrape_url_blocks_off_domain_before_dns() -> None:
    def resolver(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("off-domain URLs must be rejected before DNS resolution")

    with pytest.raises(SsrfError, match="not under an allowed domain"):
        validate_scrape_url(
            "https://evil.example.edu/research",
            ALLOWED_DOMAINS,
            resolver=resolver,
        )


@pytest.mark.parametrize(
    ("ip", "url"),
    [
        ("127.0.0.1", "https://cs.washington.edu/research"),
        ("10.1.2.3", "https://cs.washington.edu/research"),
        ("192.168.1.10", "https://cs.washington.edu/research"),
        ("169.254.169.254", "https://cs.washington.edu/research"),
        ("::1", "https://cs.washington.edu/research"),
    ],
)
def test_validate_scrape_url_blocks_allowed_domain_resolving_to_private_ip(
    ip: str,
    url: str,
) -> None:
    with pytest.raises(SsrfError, match="non-public IP"):
        validate_scrape_url(
            url,
            ALLOWED_DOMAINS,
            resolver=_resolver_for({"cs.washington.edu": ip}),
        )


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://washington.edu/research", "washington.edu"),
        ("https://cs.washington.edu/research", "cs.washington.edu"),
    ],
)
def test_validate_scrape_url_allows_public_ip_under_allowed_domain(
    url: str,
    host: str,
) -> None:
    assert (
        validate_scrape_url(
            url,
            ALLOWED_DOMAINS,
            resolver=_resolver_for({host: "93.184.216.34"}),
        )
        == host
    )
