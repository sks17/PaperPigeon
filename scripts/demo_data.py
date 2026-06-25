"""Seed a small, GROUNDED repopulation run for local demos (no network).

Loads a handful of synthetic researchers + a lab into one unpublished run, then runs the REAL
`describe_run` pipeline over it with a stub LLM — so the descriptions are grounded in genuinely
retrieved evidence (affiliation / topics / papers / co-authors / lab members), exactly as a live run
would produce. Used by `scripts/run_local_stack.py --demo` so the run-snapshot picker + provenance UI
are demonstrable without OpenAlex/OpenRouter keys. NOT product code (dev/demo only).
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.repopulation.describe_run import describe_run
from backend.repopulation.loader import load_import_rows
from backend.repopulation.models.nodes import RepopulationRun

RUN_KEY = "demo"
MODEL = "google/gemini-2.5-flash-lite"
GENERATED_AT = "2026-06-24T00:00:00+00:00"
SEED = {"institution": "Demo University", "topic": "graph machine learning", "keywords": []}

INST = "https://openalex.org/demo-INST"
LAB = "lab:demo:graph-learning-lab"

# (id suffix, name, topics, [(paper title, year)])
RESEARCHERS = [
    ("D1", "Maya Chen", ["graph neural networks", "representation learning"],
     [("Scalable Graph Representation Learning", 2024), ("Message Passing, Revisited", 2023)]),
    ("D2", "Liam Okafor", ["combinatorial optimization", "graph neural networks"],
     [("Neural Combinatorial Optimization on Graphs", 2024)]),
    ("D3", "Sofia Ramos", ["molecular machine learning", "graph neural networks"],
     [("Molecular Property Prediction with GNNs", 2023)]),
    ("D4", "Arjun Patel", ["machine learning systems", "scalable training"],
     [("Systems for Large-Scale Graph ML", 2024)]),
]
COAUTHORS = [("D1", "D2"), ("D1", "D3"), ("D2", "D4")]  # render as coauthorship links
LAB_MEMBERS = ["D1", "D2", "D3"]


def _rid(suffix: str) -> str:
    return f"https://openalex.org/demo-{suffix}"


def _node(nid, kind, name, val, attrs):
    return {"id": nid, "kind": kind, "name": name, "val": val, "orcid": None,
            "openalex_id": nid, "ror": None, "normalized_name": name.lower(),
            "attributes": attrs, "ai_description": None, "description_model": None,
            "confidence": 1.0, "source_record_key": "oa"}


def _edge(src, dst, type_, weight=1.0):
    return {"src_id": src, "dst_id": dst, "type": type_, "weight": weight, "directed": True,
            "attributes": {}, "confidence": 1.0, "source_record_key": "oa"}


def _import_rows() -> dict:
    nodes = [_node(INST, "institution", "Demo University", 3, {"country": "United States"})]
    edges = []
    for suffix, name, topics, papers in RESEARCHERS:
        rid = _rid(suffix)
        nodes.append(_node(rid, "researcher", name, 1, {
            "papers": [{"title": t, "year": y, "document_id": f"{suffix}-{y}", "tags": []} for t, y in papers],
            "tags": topics, "works_count": 10 + len(papers),
        }))
        edges.append(_edge(rid, INST, "AFFILIATED_WITH"))
    for a, b in COAUTHORS:
        edges.append(_edge(_rid(a), _rid(b), "COAUTHORED_WITH", weight=2.0))

    nodes.append(_node(LAB, "lab", "Graph Learning Lab", 2, {
        "description": "The Graph Learning Lab develops graph neural networks and optimization "
                       "methods for problems in science and systems.",
        "research_areas": ["graph neural networks", "combinatorial optimization", "ML systems"],
        "pi": "Maya Chen", "faculty": [_rid(s) for s in LAB_MEMBERS],
        "url": "https://example.edu/graph-learning-lab",
    }))
    for s in LAB_MEMBERS:
        edges.append(_edge(_rid(s), LAB, "MEMBER_OF"))

    return {
        "runs": [{"key": RUN_KEY, "seed": SEED, "status": "succeeded"}],
        "source_records": [{"key": "oa", "source": "openalex", "source_url": None,
                            "confidence": None, "evidence": "demo", "run_key": RUN_KEY,
                            "raw_s3_key": None}],
        "nodes": nodes, "edges": edges, "relevance": [],
    }


class _StubLlm:
    """Deterministic stand-in for the grounded-description model: writes a short summary from the
    node name + its topics evidence, and cites the first few real evidence ids it was shown."""

    model = MODEL

    def complete_json(self, system: str, user: str, *, model: str | None = None) -> dict:
        name_m = re.search(r'named "(.*?)"', user)
        name = name_m.group(1) if name_m else "This group"
        items = re.findall(r'^\[(\d+)\] \((\w+)\) "(.*)"\s*$', user, re.M)
        cite = [int(i) for i, _, _ in items[:3]] or [1]
        # Researchers carry a "topics" line; labs carry "areas" — both read "<label>: a, b, c.".
        focus_text = next((text for _, kind, text in items if kind in ("topics", "areas")), None)
        if focus_text and ":" in focus_text:
            focus = focus_text.split(":", 1)[1].strip().rstrip(".")
        else:
            focus = "its core research areas"
        summary = (
            f"{name} works on {focus}, with the supporting evidence drawn from the cited "
            f"affiliation, topics, and publications [{', '.join(str(c) for c in cite)}]."
        )
        return {"summary": summary, "evidence": cite, "confidence": 0.9}


def seed_demo_run(session: Session) -> dict:
    """Load the demo run and ground it. Returns {run_id, ...describe summary}."""
    load_import_rows(session, _import_rows())
    run_id = session.scalar(
        select(RepopulationRun.id).where(RepopulationRun.seed["topic"].astext == SEED["topic"])
    )
    summary = describe_run(
        session, run_id, llm=_StubLlm(), generated_at=GENERATED_AT, model=MODEL,
        kinds=("researcher", "lab"),
    )
    return {"run_id": run_id, **summary}
