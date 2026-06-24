"""End-to-end run_lab_scrape with pgserver + STUB fetcher/LLM (no network).

Proves the lab pipeline: discover → fetch (stub) → clean → extract (stub) → reconcile → load into the
existing run. Asserts a matched member becomes a MEMBER_OF edge, an unmatched member is quarantined,
the lab carries scrape provenance, and the default published (legacy) graph stays 323/1043.
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
from backend.repopulation.models.edges import Edge  # noqa: E402
from backend.repopulation.models.membership import Quarantine  # noqa: E402
from backend.repopulation.models.nodes import Node  # noqa: E402
from backend.repopulation.models.provenance import SourceRecord  # noqa: E402
from backend.repopulation.scrape_run import run_lab_scrape  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "public" / "graph_cache.json"

REPOP_SEED = {"institution": "X University", "topic": None, "keywords": [],
              "openalex_institution_id": "INST"}
RESEARCHER_SET = [{"id": "r1", "name": "Jane Smith", "normalized_name": "jane smith", "openalex_id": None}]

LAB_EXTRACTION = {
    "lab_name": "Vision Lab", "pi": "Jane Smith", "members": ["Jane Smith", "Bob Lee"],
    "research_areas": ["computer vision"], "self_description": "We study vision.",
    "source_anchor": "Vision Lab", "confidence": 0.9,
}
PAGES = {
    "https://x.edu/": '<html><body><a href="/labs/vision">Vision Lab</a></body></html>',
    "https://x.edu/labs/vision": "<html><body><h1>Vision Lab</h1><p>We study vision.</p></body></html>",
}


class StubFetcher:
    def __init__(self):
        self.skipped = []

    def fetch(self, url, *, use_cache=True):
        html = PAGES.get(url)
        if html is None:
            self.skipped.append((url, "not-found"))
            return None
        return {"body": html, "content_hash": "h:" + url, "from_cache": False}


class StubLlm:
    escalate_model = None

    def complete_json(self, system, user, *, model=None):
        return dict(LAB_EXTRACTION)


def _researcher_run_rows():
    return {
        "runs": [{"key": "run", "seed": REPOP_SEED, "status": "succeeded"}],
        "source_records": [{"key": "oa", "source": "openalex", "source_url": None,
                            "retrieved_at": None, "confidence": None, "evidence": "x",
                            "run_key": "run", "raw_s3_key": None}],
        "nodes": [{"id": "r1", "kind": "researcher", "name": "Jane Smith", "val": 1, "orcid": None,
                   "openalex_id": None, "ror": None, "normalized_name": "jane smith",
                   "attributes": {}, "ai_description": None, "description_model": None,
                   "description_generated_at": None, "description_evidence": None,
                   "confidence": 1.0, "source_record_key": "oa"}],
        "edges": [], "relevance": [],
    }


@pytest.fixture(scope="module")
def session_factory(tmp_path_factory):
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")))
    try:
        for migration in migration_files():
            srv.psql(migration.read_text(encoding="utf-8"))
        factory = make_session_factory(make_engine(srv.get_uri()))
        with factory() as s:
            load_import_rows(s, cache_to_rows(json.loads(CACHE.read_text(encoding="utf-8"))))  # run 1, published
            load_import_rows(s, _researcher_run_rows())  # run 2 (the repop run), researcher r1
        yield factory
    finally:
        srv.cleanup()


def test_lab_scrape_reconciles_members_and_stays_additive(session_factory):
    fetcher = StubFetcher()
    with session_factory() as s:
        summary = run_lab_scrape(
            s, repop_seed=REPOP_SEED, run_key="run",
            institution={"id": "INST", "ror": None, "name": "X University"},
            researcher_set=RESEARCHER_SET, homepage_url="https://x.edu/",
            allowed_domains={"x.edu"}, fetcher=fetcher, llm=StubLlm(), max_pages=5,
        )

    assert summary["lab_nodes_loaded"] == 1
    assert summary["quarantined"] == 1  # "Bob Lee" not in the researcher set

    with session_factory() as s:
        # the matched member r1 gets exactly one MEMBER_OF edge → the lab, with scrape provenance
        # (the legacy graph already has 220 MEMBER_OF edges; r1 is new, so scope to it).
        member_edges = s.scalars(
            select(Edge).where(Edge.type == "MEMBER_OF", Edge.src_id == "r1")
        ).all()
        assert len(member_edges) == 1
        assert s.get(SourceRecord, member_edges[0].source_record_id).source == "scrape"

        # the unmatched member is recorded in quarantine with a reason
        q = s.scalars(select(Quarantine).where(Quarantine.kind == "member")).all()
        assert len(q) == 1 and q[0].reason == "unmatched-researcher"

        # additive: the published (legacy) default graph is untouched
        default = graph_from_db(s)
        assert (len(default["nodes"]), len(default["links"])) == (323, 1043)


class _AimsFetcher:
    PAGES = {
        "https://x.edu/": '<html><body><a href="/labs/aims">AIMS</a></body></html>',
        "https://x.edu/labs/aims": "<html><body><h1>AIMS Lab</h1><p>scraped</p></body></html>",
    }

    def __init__(self):
        self.skipped = []

    def fetch(self, url, *, use_cache=True):
        html = self.PAGES.get(url)
        if html is None:
            self.skipped.append((url, "nf"))
            return None
        return {"body": html, "content_hash": "h:" + url, "from_cache": False}


class _AimsLlm:
    escalate_model = None

    def complete_json(self, system, user, *, model=None):
        return {"lab_name": "AIMS Lab", "pi": "Jane Smith", "members": ["Jane Smith"],
                "research_areas": ["x"], "self_description": "scraped AIMS description",
                "source_anchor": "AIMS Lab", "confidence": 0.9}


def test_scraped_lab_matching_legacy_reuses_id_without_mutating_the_legacy_node(session_factory):
    """A scraped lab whose name matches a legacy lab reuses that lab_id (MEMBER_OF targets the
    canonical lab, no duplicate node), but the published legacy node is NOT enriched/overwritten —
    ON CONFLICT(id) DO NOTHING preserves snapshot isolation."""
    with session_factory() as s:
        run_lab_scrape(
            s, repop_seed=REPOP_SEED, run_key="run",
            institution={"id": "INST", "ror": None, "name": "X University"},
            researcher_set=RESEARCHER_SET, homepage_url="https://x.edu/",
            allowed_domains={"x.edu"}, fetcher=_AimsFetcher(), llm=_AimsLlm(), max_pages=5,
        )
    with session_factory() as s:
        aims = s.get(Node, "aims_lab")  # the legacy lab node
        assert aims is not None
        assert aims.description_model is None                 # not enriched by the scrape
        assert not (aims.attributes or {}).get("description")  # legacy node attributes untouched
        # but the membership edge attached to the canonical legacy lab id
        member = s.scalars(
            select(Edge).where(Edge.type == "MEMBER_OF", Edge.dst_id == "aims_lab", Edge.src_id == "r1")
        ).all()
        assert len(member) == 1
        # no duplicate lab node was created for the scraped "AIMS Lab" (only the legacy aims_lab)
        dup = s.scalars(select(Node).where(Node.kind == "lab", Node.name == "AIMS Lab")).all()
        assert {n.id for n in dup} == {"aims_lab"}


def test_lab_scrape_is_idempotent(session_factory):
    with session_factory() as s:
        edges_before = s.scalar(select(func.count()).select_from(Edge).where(Edge.type == "MEMBER_OF"))
    with session_factory() as s:
        run_lab_scrape(
            s, repop_seed=REPOP_SEED, run_key="run",
            institution={"id": "INST", "ror": None, "name": "X University"},
            researcher_set=RESEARCHER_SET, homepage_url="https://x.edu/",
            allowed_domains={"x.edu"}, fetcher=StubFetcher(), llm=StubLlm(), max_pages=5,
        )
    with session_factory() as s:
        edges_after = s.scalar(select(func.count()).select_from(Edge).where(Edge.type == "MEMBER_OF"))
    assert edges_after == edges_before  # re-run adds no duplicate MEMBER_OF
