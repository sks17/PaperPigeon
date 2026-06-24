"""ROR client — institution name -> canonical organization (ROR id). Main-thread integration code.

Free API; from Q3 2026 a free client-ID keeps the 2000/5min limit (else 50/5min) — configurable.
"""
from __future__ import annotations

from backend.repopulation.clients.http import HttpClient
from backend.repopulation.sources.ror_parse import RorOrganization, parse_ror_organization

ROR_HOST = "api.ror.org"


class RorClient:
    BASE = "https://api.ror.org"

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def resolve(self, name: str) -> RorOrganization | None:
        """Best-match organization for a free-text institution name (ROR ranks by relevance)."""
        data, _ = self._http.get_json(f"{self.BASE}/v2/organizations", params={"query": name})
        items = data.get("items") or []
        if not items:
            return None
        return parse_ror_organization(items[0])
