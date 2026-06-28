"""Tests for the OpenAlex client budget guards and assembly behavior (P2-T08)."""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy

from backend.repopulation.clients.openalex import OpenAlexClient


class StubHttp:
    def __init__(self, responses_by_url: dict[str, list[dict]]) -> None:
        self._responses = {
            url: deque(deepcopy(responses)) for url, responses in responses_by_url.items()
        }
        self.calls: list[tuple[str, dict]] = []

    def get_json(self, url: str, params: dict | None = None) -> tuple[dict, str]:
        captured = dict(params or {})
        self.calls.append((url, captured))
        return self._responses[url].popleft(), f"raw-{len(self.calls)}"

    def params_for(self, suffix: str) -> list[dict]:
        return [params for url, params in self.calls if url.endswith(suffix)]


def _assert_no_search_param(stub: StubHttp) -> None:
    for _, params in stub.calls:
        assert "search" not in params


def test_iter_authors_uses_id_filter_cursor_pagination_and_select() -> None:
    stub = StubHttp(
        {
            "https://api.openalex.org/authors": [
                {
                    "results": [{"id": "https://openalex.org/A1"}],
                    "meta": {"next_cursor": "cursor-page-2"},
                },
                {
                    "results": [{"id": "https://openalex.org/A2"}],
                    "meta": {"next_cursor": None},
                },
            ]
        }
    )
    client = OpenAlexClient(stub)

    authors = list(
        client.iter_authors_by_institution(
            "https://openalex.org/I123", select="id,display_name"
        )
    )

    assert [author["id"] for author in authors] == [
        "https://openalex.org/A1",
        "https://openalex.org/A2",
    ]
    assert stub.params_for("/authors") == [
        {
            "filter": "last_known_institutions.id:I123",
            "cursor": "*",
            "per-page": 200,
            "select": "id,display_name",
        },
        {
            "filter": "last_known_institutions.id:I123",
            "cursor": "cursor-page-2",
            "per-page": 200,
            "select": "id,display_name",
        },
    ]
    _assert_no_search_param(stub)


def test_iter_works_uses_id_filters_and_stops_at_max_pages() -> None:
    stub = StubHttp(
        {
            "https://api.openalex.org/works": [
                {
                    "results": [{"id": "https://openalex.org/W1"}],
                    "meta": {"next_cursor": "should-not-be-followed"},
                },
                {
                    "results": [{"id": "https://openalex.org/W2"}],
                    "meta": {"next_cursor": None},
                },
            ]
        }
    )
    client = OpenAlexClient(stub)

    works = list(
        client.iter_works_by_institution(
            "https://openalex.org/I123",
            from_year=2024,
            topic_id="https://openalex.org/T456",
            select="id,title,authorships",
            max_pages=1,
        )
    )

    assert [work["id"] for work in works] == ["https://openalex.org/W1"]
    assert stub.params_for("/works") == [
        {
            "filter": (
                "authorships.institutions.id:I123,"
                "from_publication_date:2024-01-01,"
                "topics.id:T456"
            ),
            "cursor": "*",
            "per-page": 200,
            "select": "id,title,authorships",
        }
    ]
    _assert_no_search_param(stub)


def test_iter_works_by_authors_or_batches_and_combines_filters() -> None:
    stub = StubHttp(
        {
            "https://api.openalex.org/works": [
                {"results": [{"id": "https://openalex.org/W1"}], "meta": {"next_cursor": None}},
            ]
        }
    )
    client = OpenAlexClient(stub)

    works = list(
        client.iter_works_by_authors(
            ["https://openalex.org/A1", "A2"],
            from_year=2024,
            topic_id="https://openalex.org/T9",
            select="id,authorships",
            max_pages=1,
        )
    )

    assert [w["id"] for w in works] == ["https://openalex.org/W1"]
    # Cohort ids are OR'd into one filter and AND-combined with the date/topic filters.
    assert stub.params_for("/works") == [
        {
            "filter": "author.id:A1|A2,from_publication_date:2024-01-01,topics.id:T9",
            "cursor": "*",
            "per-page": 200,
            "select": "id,authorships",
        }
    ]
    _assert_no_search_param(stub)


def test_iter_works_by_authors_splits_oversized_cohorts() -> None:
    stub = StubHttp(
        {
            "https://api.openalex.org/works": [
                {"results": [], "meta": {"next_cursor": None}},
                {"results": [], "meta": {"next_cursor": None}},
            ]
        }
    )
    client = OpenAlexClient(stub)
    cohort = [f"A{i}" for i in range(OpenAlexClient.AUTHOR_FILTER_BATCH + 1)]

    list(client.iter_works_by_authors(cohort, max_pages=1))

    # 101 authors → two OR-batches → two list calls, each filtering on a slice of the cohort.
    calls = stub.params_for("/works")
    assert len(calls) == 2
    assert calls[0]["filter"].count("|") == OpenAlexClient.AUTHOR_FILTER_BATCH - 1
    assert calls[1]["filter"] == "author.id:A100"


def test_discover_authors_links_cohort_coauthors_via_their_own_works() -> None:
    """A paper co-authored by two cohort members is recoverable even though it was fetched by the
    author filter, not an institution slice — this is the fix for sparse co-authorship."""
    author_one = {"id": "https://openalex.org/A1", "display_name": "Author One"}
    author_two = {"id": "https://openalex.org/A2", "display_name": "Author Two"}
    shared = {
        "id": "https://openalex.org/W_SHARED",
        "authorships": [
            {"author": {"id": "https://openalex.org/A1"}},
            {"author": {"id": "https://openalex.org/A2"}},
        ],
    }
    stub = StubHttp(
        {
            "https://api.openalex.org/authors": [
                {"results": [author_one, author_two], "meta": {"next_cursor": None}},
            ],
            # The same paper is returned in both author batches — must be attached, not duplicated.
            "https://api.openalex.org/works": [
                {"results": [shared], "meta": {"next_cursor": None}},
            ],
        }
    )
    client = OpenAlexClient(stub)

    discovered = client.discover_authors(
        "https://openalex.org/I123", max_author_pages=1, max_work_pages=1
    )

    works_by_author = {a["id"]: [w["id"] for w in a["recent_works"]] for a in discovered}
    assert works_by_author == {
        "https://openalex.org/A1": ["https://openalex.org/W_SHARED"],
        "https://openalex.org/A2": ["https://openalex.org/W_SHARED"],
    }
    # Works are fetched by author id, never by institution.
    assert stub.params_for("/works")[0]["filter"].startswith("author.id:")


def test_discover_authors_attaches_recent_works_from_authorships() -> None:
    author_one = {"id": "https://openalex.org/A1", "display_name": "Author One"}
    author_two = {"id": "https://openalex.org/A2", "display_name": "Author Two"}
    work_one = {
        "id": "https://openalex.org/W1",
        "authorships": [
            {"author": {"id": "https://openalex.org/A1"}},
            {"author": {"id": "https://openalex.org/A3"}},
        ],
    }
    work_two = {
        "id": "https://openalex.org/W2",
        "authorships": [
            {"author": {"id": "https://openalex.org/A1"}},
            {"author": {"id": "https://openalex.org/A2"}},
        ],
    }
    stub = StubHttp(
        {
            "https://api.openalex.org/authors": [
                {"results": [author_one, author_two], "meta": {"next_cursor": None}},
            ],
            "https://api.openalex.org/works": [
                {"results": [work_one, work_two], "meta": {"next_cursor": None}},
            ],
        }
    )
    client = OpenAlexClient(stub)

    discovered = client.discover_authors(
        "https://openalex.org/I123",
        from_year=2024,
        topic_id="https://openalex.org/T456",
        max_author_pages=1,
        max_work_pages=1,
    )

    works_by_author = defaultdict(list)
    for author in discovered:
        works_by_author[author["id"]] = [
            work["id"] for work in author["recent_works"]
        ]

    assert works_by_author == {
        "https://openalex.org/A1": [
            "https://openalex.org/W1",
            "https://openalex.org/W2",
        ],
        "https://openalex.org/A2": ["https://openalex.org/W2"],
    }
    assert stub.params_for("/authors")[0]["select"] == OpenAlexClient.AUTHOR_SELECT
    assert stub.params_for("/works")[0]["select"] == OpenAlexClient.WORK_SELECT
    _assert_no_search_param(stub)
