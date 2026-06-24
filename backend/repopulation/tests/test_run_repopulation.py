"""P2-T09: end-to-end run_repopulation with STUB clients + pgserver.

Drives the full discovery pipeline (run.py) against an in-process Postgres, injecting stub ROR /
OpenAlex clients that return canned dicts (no network, no real API calls) and `embeddings=None`.
Asserts the run succeeds, stays isolated from the default published (legacy) snapshot, surfaces the
discovered researchers in its own run snapshot, writes one relevance row per researcher, and is
idempotent when the same seed is re-run.

Mirrors the pgserver fixture pattern in tests/test_snapshot_isolation.py / test_api_graph_contract.py.
Run by the main thread (`python -m pytest -q` from the project root).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pgserver")

import pgserver  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import graph_from_db, load_import_rows  # noqa: E402
from backend.repopulation.models.nodes import Relevance, RepopulationRun  # noqa: E402
from backend.repopulation.run import run_repopulation  # noqa: E402
from backend.repopulation.sources.ror_parse import RorOrganization  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "public" / "graph_cache.json"

CURRENT_YEAR = 2026
LEGACY_COUNTS = (323, 1043)  # nodes, links of the published legacy snapshot

SEED = {
    "institution": "Test University",
    "topic": "graph theory",
    "keywords": ["graphs", "networks"],
}

# Canned API payloads (the shapes the live clients would have returned, post-parse-ready).
ROR_ORG = RorOrganization(
    id="https://ror.org/01testuniv",
    name="Test University",
    country="United States",
)
OPENALEX_INSTITUTION = {
    "id": "https://openalex.org/I100",
    "display_name": "Test University",
}
RESEARCHER_IDS = {"https://openalex.org/A1", "https://openalex.org/A2"}
RAW_AUTHORS = [
    {
        "id": "https://openalex.org/A1",
        "display_name": "Alice Example",
        "ids": {"orcid": "https://orcid.org/0000-0000-0000-0001"},
        "last_known_institution": {
            "id": "https://openalex.org/I100",
            "ror": "https://ror.org/01testuniv",
            "display_name": "Test University",
        },
        "works_count": 42,
        "cited_by_count": 100,
        "summary_stats": {"h_index": 10, "i10_index": 5},
        "topics": [
            {
                "id": "https://openalex.org/T1",
                "display_name": "Graph Theory",
                "score": 0.8,
                "field": {"display_name": "Mathematics"},
            }
        ],
        "recent_works": [
            {
                "id": "https://openalex.org/W1",
                "title": "A Shared Paper on Graphs",
                "publication_year": 2024,
                "cited_by_count": 3,
                "topics": [{"id": "https://openalex.org/T1", "display_name": "Graph Theory"}],
            },
            {
                "id": "https://openalex.org/W2",
                "title": "Alice Solo Paper",
                "publication_year": 2023,
            },
        ],
    },
    {
        "id": "https://openalex.org/A2",
        "display_name": "Bob Example",
        "ids": {"orcid": "https://orcid.org/0000-0000-0000-0002"},
        "last_known_institution": {
            "id": "https://openalex.org/I100",
            "ror": "https://ror.org/01testuniv",
            "display_name": "Test University",
        },
        "works_count": 17,
        "summary_stats": {"h_index": 4},
        "topics": [
            {"id": "https://openalex.org/T1", "display_name": "Graph Theory", "score": 0.5}
        ],
        "recent_works": [
            {
                "id": "https://openalex.org/W1",
                "title": "A Shared Paper on Graphs",
                "publication_year": 2024,
            }
        ],
    },
]


class _StubRor:
    """Stand-in for the live ROR client: resolve() returns a canned RorOrganization."""

    def __init__(self, org: RorOrganization) -> None:
        self._org = org
        self.resolved: list[str] = []

    def resolve(self, name: str) -> RorOrganization:
        self.resolved.append(name)
        return self._org


class _StubOpenAlex:
    """Stand-in for the live OpenAlex client: returns canned dicts, records no network."""

    def __init__(self, institution: dict, authors: list[dict]) -> None:
        self._institution = institution
        self._authors = authors
        self.calls: list[tuple] = []

    def get_institution_by_ror(self, ror_id: str) -> dict:
        self.calls.append(("get_institution_by_ror", ror_id))
        return self._institution

    def discover_authors(
        self,
        institution_id: str,
        *,
        from_year: int,
        max_author_pages: int,
        max_work_pages: int,
    ) -> list[dict]:
        self.calls.append(("discover_authors", institution_id, from_year))
        # Return fresh copies so the pipeline can never mutate the canned fixtures.
        return [json.loads(json.dumps(author)) for author in self._authors]


def _make_clients() -> tuple[_StubRor, _StubOpenAlex]:
    return _StubRor(ROR_ORG), _StubOpenAlex(OPENALEX_INSTITUTION, RAW_AUTHORS)


def _counts(graph: dict) -> tuple[int, int]:
    return len(graph["nodes"]), len(graph["links"])


@pytest.fixture(scope="module")
def session_factory(tmp_path_factory: pytest.TempPathFactory):
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")))
    try:
        for migration in migration_files():
            srv.psql(migration.read_text(encoding="utf-8"))

        factory = make_session_factory(make_engine(srv.get_uri()))
        with factory() as session:
            legacy_graph = json.loads(CACHE.read_text(encoding="utf-8"))
            load_import_rows(session, cache_to_rows(legacy_graph))
        yield factory
    finally:
        srv.cleanup()


@pytest.fixture(scope="module")
def first_run(session_factory):
    ror, openalex = _make_clients()
    with session_factory() as session:
        result = run_repopulation(
            session, dict(SEED), ror=ror, openalex=openalex, current_year=CURRENT_YEAR
        )
    return result


def test_run_marked_succeeded(session_factory, first_run) -> None:
    run_id = first_run["run_id"]
    assert run_id is not None
    with session_factory() as session:
        run = session.get(RepopulationRun, run_id)
        assert run is not None
        assert run.status == "succeeded"


def test_default_snapshot_unchanged_by_unpublished_run(session_factory, first_run) -> None:
    with session_factory() as session:
        assert _counts(graph_from_db(session)) == LEGACY_COUNTS


def test_run_snapshot_shows_discovered_researchers(session_factory, first_run) -> None:
    assert first_run["authors_discovered"] == len(RESEARCHER_IDS)
    with session_factory() as session:
        run_graph = graph_from_db(session, run_id=first_run["run_id"])
    rendered_ids = {node["id"] for node in run_graph["nodes"]}
    assert rendered_ids == RESEARCHER_IDS
    assert all(node["type"] == "researcher" for node in run_graph["nodes"])
    # The shared work yields one rendered co-authorship link (COAUTHORED_WITH -> "paper").
    assert all(link["type"] == "paper" for link in run_graph["links"])
    assert len(run_graph["links"]) >= 1


def test_relevance_row_per_researcher(session_factory, first_run) -> None:
    assert first_run["relevance_rows"] == first_run["authors_discovered"]
    with session_factory() as session:
        run_relevance = session.scalar(
            select(func.count())
            .select_from(Relevance)
            .where(Relevance.run_id == first_run["run_id"])
        )
    assert run_relevance == len(RESEARCHER_IDS)


def test_rerunning_same_seed_is_idempotent(session_factory, first_run) -> None:
    with session_factory() as session:
        graph_before = graph_from_db(session, run_id=first_run["run_id"])
        default_before = graph_from_db(session)

    ror, openalex = _make_clients()
    with session_factory() as session:
        second = run_repopulation(
            session, dict(SEED), ror=ror, openalex=openalex, current_year=CURRENT_YEAR
        )

    # Same run row reused; no duplicated nodes/edges/relevance/source rows across the DB.
    assert second["run_id"] == first_run["run_id"]
    assert second["counts"] == first_run["counts"]

    with session_factory() as session:
        graph_after = graph_from_db(session, run_id=first_run["run_id"])
        default_after = graph_from_db(session)

    assert graph_after == graph_before
    assert _counts(default_after) == LEGACY_COUNTS
    assert default_after == default_before
