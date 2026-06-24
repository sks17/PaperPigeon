"""Tests for `build_lab_rows` (P3-T06), checked against SCRAPING.md §3.

Feeds a small set of validated `LabExtraction`s + a researcher set + a legacy lab list to
`build_lab_rows` and asserts the reconciliation / quarantine / legacy-merge contract:
- a member whose normalized name uniquely matches a researcher -> a MEMBER_OF edge;
- an unmatched member -> quarantined (kind "member"), with NO edge;
- a low-confidence lab and an ungrounded (no source_anchor) lab -> quarantined whole, absent from
  `accepted` ("no evidence -> no claim");
- a lab whose name matches a legacy display name reuses that legacy lab_id;
- every accepted node/edge carries a source_record_key resolving to a returned source_record, plus
  provenance + a numeric weight on edges.

Pure: no DB, no network. The transform receives already-parsed dataclasses + plain dicts.
"""
from __future__ import annotations

from backend.repopulation.discovery.build_lab_rows import MEMBER_OF, build_lab_rows, normalize
from backend.repopulation.extraction.lab_schema import LabExtraction

INSTITUTION = {
    "id": "https://openalex.org/I_TEST",
    "ror": "https://ror.org/01an7q238",
    "name": "Test University",
}
RUN_KEY = "run-scrape-test"
SOURCE_KEYS = {"scrape": "scrape"}

RESEARCHER_SET = [
    {"id": "R-ada", "name": "Ada Lovelace", "normalized_name": "ada lovelace", "openalex_id": "A-ada"},
    {"id": "R-grace", "name": "Grace Hopper", "normalized_name": "grace hopper", "openalex_id": "A-grace"},
]

# "Allen NLP Lab" matches a scraped lab by display name -> the scraped lab must reuse this id.
LEGACY_LABS = [
    ("lab-allen-nlp", "Allen NLP Lab"),
    ("lab-some-other", "Some Other Lab"),
]

ROBOTICS_LAB_ID = f"lab:{INSTITUTION['id']}:{normalize('Robotics Lab')}"
ALLEN_LAB_ID = "lab-allen-nlp"


def _item(extraction: LabExtraction, source_url: str, raw_key: str) -> dict:
    return {
        "extraction": extraction,
        "source_url": source_url,
        "raw_key": raw_key,
        "anchor": extraction.source_anchor,
    }


def _extractions() -> list[dict]:
    robotics = LabExtraction(
        lab_name="Robotics Lab",
        pi="Ada Lovelace",
        members=("Ada Lovelace", "Nobody McGhost"),  # one match, one miss
        research_areas=("robotics",),
        self_description="We build robots.",
        source_anchor="Robotics Lab — People",
        confidence=0.9,
    )
    allen_nlp = LabExtraction(
        lab_name="Allen NLP Lab",  # matches a legacy lab display name
        pi="Grace Hopper",
        members=("Grace Hopper",),
        research_areas=("natural language processing",),
        self_description="We study language.",
        source_anchor="Allen NLP Lab — Members",
        confidence=0.8,
    )
    low_confidence = LabExtraction(
        lab_name="Ghost Lab",
        pi=None,
        members=(),
        research_areas=(),
        self_description=None,
        source_anchor="Ghost Lab heading",
        confidence=0.2,  # below default min_confidence (0.5)
    )
    no_anchor = LabExtraction(
        lab_name="Anchorless Lab",
        pi=None,
        members=(),
        research_areas=(),
        self_description="Confident but ungrounded.",
        source_anchor=None,  # no grounding evidence
        confidence=0.95,
    )
    return [
        _item(robotics, "https://cs.test.edu/robotics", "hash-robotics"),
        _item(allen_nlp, "https://cs.test.edu/nlp", "hash-nlp"),
        _item(low_confidence, "https://cs.test.edu/ghost", "hash-ghost"),
        _item(no_anchor, "https://cs.test.edu/anchorless", "hash-anchorless"),
    ]


def _build() -> dict:
    return build_lab_rows(
        _extractions(), INSTITUTION, RESEARCHER_SET, LEGACY_LABS, RUN_KEY, SOURCE_KEYS
    )


def _lab_nodes(accepted: dict) -> list[dict]:
    return [n for n in accepted["nodes"] if n["kind"] == "lab"]


def _edges_of_type(accepted: dict, edge_type: str) -> list[dict]:
    return [e for e in accepted["edges"] if e["type"] == edge_type]


def test_accepted_labs_and_legacy_id_reuse() -> None:
    accepted = _build()["accepted"]
    labs = _lab_nodes(accepted)

    lab_ids = {n["id"] for n in labs}
    assert ROBOTICS_LAB_ID in lab_ids
    assert ALLEN_LAB_ID in lab_ids  # legacy lab_id reused, not a synthesised one

    # The Allen lab node really carries the legacy id, keyed by its normalized name.
    (allen,) = [n for n in labs if normalize(n["name"]) == normalize("Allen NLP Lab")]
    assert allen["id"] == ALLEN_LAB_ID

    # Quarantined labs never become nodes.
    accepted_names = {normalize(n["name"]) for n in labs}
    assert normalize("Ghost Lab") not in accepted_names
    assert normalize("Anchorless Lab") not in accepted_names


def test_matched_member_creates_member_of_edge() -> None:
    accepted = _build()["accepted"]
    member_edges = {(e["src_id"], e["dst_id"]) for e in _edges_of_type(accepted, MEMBER_OF)}

    assert ("R-ada", ROBOTICS_LAB_ID) in member_edges
    assert ("R-grace", ALLEN_LAB_ID) in member_edges


def test_unmatched_member_is_quarantined_with_no_edge() -> None:
    result = _build()
    accepted, quarantined = result["accepted"], result["quarantined"]

    member_quarantines = [q for q in quarantined if q["kind"] == "member"]
    ghost = [q for q in member_quarantines if q["payload"]["member"] == "Nobody McGhost"]
    assert len(ghost) == 1
    assert ghost[0]["reason"] == "unmatched-researcher"
    assert ghost[0]["payload"]["lab"] == "Robotics Lab"

    # The unmatched member produced no edge: only Ada is wired to the robotics lab.
    robotics_member_edges = [
        e for e in _edges_of_type(accepted, MEMBER_OF) if e["dst_id"] == ROBOTICS_LAB_ID
    ]
    assert len(robotics_member_edges) == 1
    assert robotics_member_edges[0]["src_id"] == "R-ada"


def test_low_confidence_and_ungrounded_labs_are_quarantined() -> None:
    quarantined = _build()["quarantined"]
    lab_quarantines = {
        q["payload"]["lab"]: q["reason"] for q in quarantined if q["kind"] == "lab"
    }

    assert lab_quarantines.get("Ghost Lab") == "low-confidence"
    assert lab_quarantines.get("Anchorless Lab") == "missing-source-anchor"


def test_every_accepted_node_and_edge_has_resolvable_provenance() -> None:
    accepted = _build()["accepted"]

    source_keys = {s["key"] for s in accepted["source_records"]}
    assert source_keys, "expected at least one scrape source_record"
    for record in accepted["source_records"]:
        assert record["source"] == "scrape"
        assert record["run_key"] == RUN_KEY

    for node in accepted["nodes"]:
        assert node["source_record_key"] in source_keys, node

    for edge in accepted["edges"]:
        assert edge["source_record_key"] in source_keys, edge
        assert isinstance(edge["weight"], float)
        assert isinstance(edge["directed"], bool)


def test_build_is_deterministic() -> None:
    assert _build() == _build()
