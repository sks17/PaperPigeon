"""OpenAlex client — institution -> authors -> works/topics. Main-thread integration code.

BUDGET-AWARE (validated 2026 usage model): API key mandatory; ID filters only (never `search=`);
cursor pagination at per-page=200; `select=` to trim fields; the shared HttpClient's raw-store
cache means re-runs don't re-bill. A correctly-batched single-institution sweep stays inside the
free $1/day envelope.
"""
from __future__ import annotations

from collections.abc import Iterator

from backend.repopulation.clients.budget import OPENALEX_LIST_COST
from backend.repopulation.clients.http import HttpClient

OPENALEX_HOST = "api.openalex.org"


def short_id(entity_id: str) -> str:
    """'https://openalex.org/I201448701' -> 'I201448701' (filters accept the short id)."""
    return entity_id.rstrip("/").rsplit("/", 1)[-1]


class OpenAlexClient:
    BASE = "https://api.openalex.org"

    def __init__(self, http: HttpClient, api_key: str | None = None, budget=None) -> None:
        self._http = http
        self._api_key = api_key
        self._budget = budget

    def _params(self, **kw) -> dict:
        if self._api_key:
            kw["api_key"] = self._api_key
        return kw

    def get_institution_by_ror(self, ror_id: str) -> dict:
        ror = short_id(ror_id)
        data, _ = self._http.get_json(f"{self.BASE}/institutions/ror:{ror}", params=self._params())
        return data

    def iter_authors_by_institution(
        self, institution_id: str, *, select: str | None = None, per_page: int = 200, max_pages: int = 50
    ) -> Iterator[dict]:
        yield from self._cursor(
            "authors",
            filter=f"last_known_institutions.id:{short_id(institution_id)}",
            select=select,
            per_page=per_page,
            max_pages=max_pages,
        )

    def iter_works_by_institution(
        self,
        institution_id: str,
        *,
        from_year: int | None = None,
        topic_id: str | None = None,
        select: str | None = None,
        per_page: int = 200,
        max_pages: int = 50,
    ) -> Iterator[dict]:
        filters = [f"authorships.institutions.id:{short_id(institution_id)}"]
        if from_year is not None:
            filters.append(f"from_publication_date:{from_year}-01-01")
        if topic_id is not None:
            filters.append(f"topics.id:{short_id(topic_id)}")
        yield from self._cursor(
            "works", filter=",".join(filters), select=select, per_page=per_page, max_pages=max_pages
        )

    # Author objects don't carry their works; assemble them so each author dict has the
    # `recent_works` key the parser + build_rows expect (also the source of co-authorship).
    AUTHOR_SELECT = (
        "id,display_name,orcid,last_known_institutions,topics,works_count,"
        "cited_by_count,summary_stats"
    )
    WORK_SELECT = "id,title,publication_year,doi,cited_by_count,authorships,topics"

    def discover_authors(
        self,
        institution_id: str,
        *,
        from_year: int | None = None,
        topic_id: str | None = None,
        max_author_pages: int = 50,
        max_work_pages: int = 50,
    ) -> list[dict]:
        """Authors at the institution, each with `recent_works` attached from the works sweep."""
        authors: dict[str, dict] = {}
        for author in self.iter_authors_by_institution(
            institution_id, select=self.AUTHOR_SELECT, max_pages=max_author_pages
        ):
            author["recent_works"] = []
            authors[author["id"]] = author

        for work in self.iter_works_by_institution(
            institution_id, from_year=from_year, topic_id=topic_id,
            select=self.WORK_SELECT, max_pages=max_work_pages,
        ):
            for authorship in work.get("authorships", []):
                author_id = (authorship.get("author") or {}).get("id")
                if author_id in authors:
                    authors[author_id]["recent_works"].append(work)

        return list(authors.values())

    def _cursor(self, entity: str, *, filter: str, select, per_page, max_pages) -> Iterator[dict]:
        cursor = "*"
        pages = 0
        while cursor and pages < max_pages:
            # List+filter requests are the billable OpenAlex op; charge before the call so an
            # over-budget sweep stops cleanly (single-entity lookups elsewhere are $0).
            if self._budget is not None:
                self._budget.charge(OPENALEX_LIST_COST, f"openalex {entity} page")
            params = self._params(filter=filter, cursor=cursor)
            params["per-page"] = per_page
            if select:
                params["select"] = select
            data, _ = self._http.get_json(f"{self.BASE}/{entity}", params=params)
            for result in data.get("results", []):
                yield result
            cursor = (data.get("meta") or {}).get("next_cursor")
            pages += 1
