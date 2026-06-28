"""Tests for `build_import_rows` (P2-T03), checked against DISCOVERY.md + SCHEMA.md §1–2.

Parses the saved OpenAlex/ROR fixtures via the existing pure parsers, feeds the resulting
dataclasses to `build_import_rows`, and asserts the ImportRows contract: node kinds/vals,
provenance resolution, the WORKS_ON / COAUTHORED_WITH edges, dedup keys, and empty relevance.

The shipped author fixture has a single researcher, so co-authorship cannot be exercised from it
alone; the COAUTHORED_WITH test synthesises a second author (in-memory, by editing only this test
file) that shares works with the first to verify `weight == #joint works`.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from backend.repopulation.discovery.build_rows import NODE_VAL, build_import_rows
from backend.repopulation.sources.openalex_parse import parse_openalex_author
from backend.repopulation.sources.ror_parse import parse_ror_organization


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "backend" / "repopulation" / "tests" / "fixtures"
OPENALEX_AUTHOR_FIXTURE = FIXTURES / "openalex_author_fixture.json"
ROR_ORG_FIXTURE = FIXTURES / "ror_organization_fixture.json"

OPENALEX_INSTITUTION_ID = "https://openalex.org/I201448701"
RUN_KEY = "run-test"
SOURCE_KEYS = {"openalex": "src-openalex", "ror": "src-ror"}
SEED = {
    "institution": "University of Washington",
    "topic": None,
    "keywords": [],
    "openalex_institution_id": OPENALEX_INSTITUTION_ID,
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_from_fixtures(authors):
    institution = parse_ror_organization(_load_json(ROR_ORG_FIXTURE))
    return build_import_rows(institution, tuple(authors), SEED, RUN_KEY, SOURCE_KEYS)


def _nodes_of_kind(rows: dict, kind: str) -> list[dict]:
    return [node for node in rows["nodes"] if node["kind"] == kind]


def _edges_of_type(rows: dict, edge_type: str) -> list[dict]:
    return [edge for edge in rows["edges"] if edge["type"] == edge_type]


def _author_payload(author_id: str, orcid: str, name: str, work_ids: list[str]) -> dict:
    """A minimal OpenAlex author payload derived from the shipped fixture, with a chosen set of
    recent works (so two synthesised authors can share works for the co-authorship test)."""
    payload = copy.deepcopy(_load_json(OPENALEX_AUTHOR_FIXTURE))
    payload["id"] = f"https://openalex.org/{author_id}"
    payload["ids"] = {
        "openalex": payload["id"],
        "orcid": f"https://orcid.org/{orcid}",
    }
    payload["display_name"] = name
    payload["recent_works"] = [
        {
            "id": f"https://openalex.org/{work_id}",
            "title": work_id,
            "publication_year": 2025,
        }
        for work_id in work_ids
    ]
    return payload


def test_node_kinds_and_vals_follow_the_convention() -> None:
    author = parse_openalex_author(_load_json(OPENALEX_AUTHOR_FIXTURE))
    rows = _build_from_fixtures([author])

    for node in rows["nodes"]:
        assert node["kind"] in NODE_VAL, f"unknown node kind: {node['kind']}"
        assert node["val"] == NODE_VAL[node["kind"]], node

    present_kinds = {node["kind"] for node in rows["nodes"]}
    assert {"institution", "researcher", "topic", "paper"} <= present_kinds


def test_every_node_and_edge_resolves_to_a_returned_source_record() -> None:
    author = parse_openalex_author(_load_json(OPENALEX_AUTHOR_FIXTURE))
    rows = _build_from_fixtures([author])

    source_record_keys = {record["key"] for record in rows["source_records"]}
    # One source_record per source: OpenAlex + ROR, keyed by the supplied provenance keys.
    assert source_record_keys == set(SOURCE_KEYS.values())
    assert len(rows["source_records"]) == 2

    for node in rows["nodes"]:
        assert node["source_record_key"] in source_record_keys, node
    for edge in rows["edges"]:
        assert edge["source_record_key"] in source_record_keys, edge


def test_dedup_keys_are_populated() -> None:
    author = parse_openalex_author(_load_json(OPENALEX_AUTHOR_FIXTURE))
    rows = _build_from_fixtures([author])

    (researcher,) = _nodes_of_kind(rows, "researcher")
    assert researcher["id"] == "https://openalex.org/A5072005348"
    assert researcher["orcid"] == "https://orcid.org/0000-0002-1825-0097"
    assert researcher["openalex_id"]
    assert researcher["normalized_name"]

    (institution,) = _nodes_of_kind(rows, "institution")
    assert institution["id"] == OPENALEX_INSTITUTION_ID
    assert institution["ror"] == "https://ror.org/01an7q238"
    assert institution["openalex_id"]
    assert institution["normalized_name"]


def test_works_on_edge_present_with_topic_share_weight() -> None:
    author = parse_openalex_author(_load_json(OPENALEX_AUTHOR_FIXTURE))
    rows = _build_from_fixtures([author])

    works_on = _edges_of_type(rows, "WORKS_ON")
    assert works_on, "expected at least one WORKS_ON edge"

    researcher_id = "https://openalex.org/A5072005348"
    topic_id = "https://openalex.org/T10191"
    edge = next(
        edge
        for edge in works_on
        if edge["src_id"] == researcher_id and edge["dst_id"] == topic_id
    )
    # WORKS_ON weight is the topic share == the author topic's score in the fixture.
    assert edge["weight"] == 0.96


def test_affiliated_with_and_authored_edges_present() -> None:
    author = parse_openalex_author(_load_json(OPENALEX_AUTHOR_FIXTURE))
    rows = _build_from_fixtures([author])

    researcher_id = "https://openalex.org/A5072005348"

    affiliations = _edges_of_type(rows, "AFFILIATED_WITH")
    assert any(
        edge["src_id"] == researcher_id and edge["dst_id"] == OPENALEX_INSTITUTION_ID
        for edge in affiliations
    )

    authored = _edges_of_type(rows, "AUTHORED")
    assert any(edge["src_id"] == researcher_id for edge in authored)


def test_relevance_is_empty() -> None:
    author = parse_openalex_author(_load_json(OPENALEX_AUTHOR_FIXTURE))
    rows = _build_from_fixtures([author])

    assert rows["relevance"] == []


def test_coauthored_with_weight_equals_number_of_joint_works() -> None:
    shared_works = ["W_SHARED_1", "W_SHARED_2"]
    author_a = parse_openalex_author(
        _author_payload("A1111", "0000-0002-0000-0001", "Author A", shared_works + ["W_A"])
    )
    author_b = parse_openalex_author(
        _author_payload("A2222", "0000-0002-0000-0002", "Author B", shared_works + ["W_B"])
    )

    rows = _build_from_fixtures([author_a, author_b])

    id_a = "https://openalex.org/A1111"
    id_b = "https://openalex.org/A2222"
    coauthored = [
        edge
        for edge in _edges_of_type(rows, "COAUTHORED_WITH")
        if {edge["src_id"], edge["dst_id"]} == {id_a, id_b}
    ]
    # One undirected pair → exactly one directed edge (ordered by author id).
    assert len(coauthored) == 1
    assert coauthored[0]["weight"] == len(shared_works)


def test_estimated_lab_emitted_for_a_coauthoring_group() -> None:
    """A connected group of researchers yields an estimated lab + MEMBER_OF edges through the full
    builder — the fallback that gives a novel university lab affiliations without scraping."""
    shared = ["W_GROUP_1", "W_GROUP_2"]
    authors = [
        parse_openalex_author(
            _author_payload(f"A{i}", f"0000-0002-0000-000{i}", f"Author {i}", shared)
        )
        for i in range(1, 4)
    ]

    rows = _build_from_fixtures(authors)

    labs = _nodes_of_kind(rows, "lab")
    assert len(labs) == 1
    assert labs[0]["attributes"]["estimated"] is True
    assert labs[0]["confidence"] < 1.0

    member_edges = _edges_of_type(rows, "MEMBER_OF")
    assert len(member_edges) == 3
    assert {e["dst_id"] for e in member_edges} == {labs[0]["id"]}

    # The estimate carries its own provenance record (source 'ai') alongside OpenAlex + ROR.
    assert labs[0]["attributes"]["method"] == "coauthorship"
    estimate_key = labs[0]["source_record_key"]
    keys = {s["key"]: s for s in rows["source_records"]}
    assert estimate_key in keys
    assert keys[estimate_key]["source"] == "ai"
