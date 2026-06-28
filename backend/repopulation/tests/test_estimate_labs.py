"""Tests for estimated lab affiliations derived from co-authorship (discovery/estimate_labs.py).

The estimator is pure and deterministic: it turns the cohort + its COAUTHORED_WITH weight map into
estimated `lab` nodes and `MEMBER_OF` edges. These tests pin the contract that fixes the "no lab
affiliations even estimated" gap — a connected research group yields a lab anchored on its most
senior member, small noise pairs do not, and the output is reproducible.
"""
from __future__ import annotations

from backend.repopulation.discovery.estimate_labs import (
    MEMBER_OF,
    MIN_LAB_SIZE,
    estimate_labs,
)
from backend.repopulation.sources.openalex_parse import OpenAlexAuthor, OpenAlexTopic

INSTITUTION = "https://openalex.org/I123"


def _author(short: str, *, h_index: int = 1, works: int = 10, topics=()):
    return OpenAlexAuthor(
        id=f"https://openalex.org/{short}",
        orcid=None,
        display_name=f"Dr {short}",
        last_known_institution=None,
        topics=tuple(
            OpenAlexTopic(id=f"T_{name}", display_name=name, score=score)
            for name, score in topics
        ),
        recent_works=(),
        works_count=works,
        cited_by_count=None,
        h_index=h_index,
        i10_index=None,
    )


def _clique_weights(short_ids: list[str], weight: int = 2) -> dict[tuple[str, str], int]:
    """Fully-connected co-authorship among the given authors (ordered src<dst, as build_rows emits)."""
    ids = sorted(f"https://openalex.org/{s}" for s in short_ids)
    weights: dict[tuple[str, str], int] = {}
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            weights[(ids[i], ids[j])] = weight
    return weights


def test_connected_group_becomes_one_estimated_lab() -> None:
    authors = (
        _author("A1", h_index=30, topics=[("Machine Learning", 0.9)]),
        _author("A2", h_index=5, topics=[("Machine Learning", 0.8)]),
        _author("A3", h_index=3, topics=[("Robotics", 0.4)]),
    )
    weights = _clique_weights(["A1", "A2", "A3"])

    result = estimate_labs(INSTITUTION, authors, weights, "run", "estimate")

    labs = result["nodes"]
    assert len(labs) == 1
    lab = labs[0]
    assert lab["kind"] == "lab"
    assert lab["val"] == 2
    # Anchored on the most senior member (highest h-index).
    assert lab["id"] == f"lab:estimated:{INSTITUTION}:A1"
    assert lab["name"] == "Dr A1 Group"
    assert lab["attributes"]["estimated"] is True
    assert lab["attributes"]["pi"] == "Dr A1"
    assert sorted(lab["attributes"]["faculty"]) == [
        "https://openalex.org/A1",
        "https://openalex.org/A2",
        "https://openalex.org/A3",
    ]
    # Dominant shared topic surfaces as a research area.
    assert lab["attributes"]["research_areas"][0] == "Machine Learning"


def test_member_of_edge_per_member_with_estimated_flag() -> None:
    authors = tuple(_author(f"A{i}") for i in range(1, 4))
    weights = _clique_weights(["A1", "A2", "A3"])

    result = estimate_labs(INSTITUTION, authors, weights, "run", "estimate")

    edges = result["edges"]
    assert len(edges) == 3
    assert {e["type"] for e in edges} == {MEMBER_OF}
    lab_id = result["nodes"][0]["id"]
    assert {e["dst_id"] for e in edges} == {lab_id}
    assert all(e["attributes"]["estimated"] is True for e in edges)
    assert all(e["confidence"] < 1.0 for e in edges)
    # A provenance record is emitted only when labs are produced.
    assert len(result["source_records"]) == 1
    assert result["source_records"][0]["source"] == "ai"  # closed-vocabulary; method on attrs
    assert result["nodes"][0]["attributes"]["method"] == "coauthorship"


def test_pair_below_min_size_produces_no_lab() -> None:
    assert MIN_LAB_SIZE >= 3  # guard: a lone pair is too weak to assert a lab
    authors = (_author("A1"), _author("A2"))
    weights = _clique_weights(["A1", "A2"])

    result = estimate_labs(INSTITUTION, authors, weights, "run", "estimate")

    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["source_records"] == []


def test_two_separate_groups_yield_two_labs() -> None:
    authors = tuple(_author(f"A{i}") for i in range(1, 7))
    weights = {**_clique_weights(["A1", "A2", "A3"]), **_clique_weights(["A4", "A5", "A6"])}

    result = estimate_labs(INSTITUTION, authors, weights, "run", "estimate")

    assert len(result["nodes"]) == 2
    faculties = sorted(tuple(sorted(n["attributes"]["faculty"])) for n in result["nodes"])
    assert faculties[0] == (
        "https://openalex.org/A1",
        "https://openalex.org/A2",
        "https://openalex.org/A3",
    )
    assert faculties[1] == (
        "https://openalex.org/A4",
        "https://openalex.org/A5",
        "https://openalex.org/A6",
    )


def test_is_deterministic() -> None:
    authors = tuple(_author(f"A{i}", h_index=i) for i in range(1, 6))
    weights = _clique_weights(["A1", "A2", "A3", "A4", "A5"])

    first = estimate_labs(INSTITUTION, authors, weights, "run", "estimate")
    second = estimate_labs(INSTITUTION, authors, weights, "run", "estimate")

    assert first == second


def test_no_coauthorship_yields_nothing() -> None:
    authors = (_author("A1"), _author("A2"), _author("A3"))

    result = estimate_labs(INSTITUTION, authors, {}, "run", "estimate")

    assert result == {"nodes": [], "edges": [], "source_records": []}
