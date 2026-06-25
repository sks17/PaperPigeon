"""P4: description grounding + legacy-preservation gate (pure).

build_description_update / evaluate_description accept a grounded, confident, non-legacy description
and reject (with a reason) when: the node already has a legacy DynamoDB `about` (never overwrite),
the model's confidence is below threshold, or the description is ungrounded (no citations, or a
citation that wasn't in the evidence shown to the model = a hallucinated id).
"""
from __future__ import annotations

from backend.repopulation.descriptions.build_rows import (
    build_description_update,
    evaluate_description,
)
from backend.repopulation.extraction.description_schema import NodeDescription

NODE = {"id": "https://openalex.org/A1", "description_model": None}
LEGACY_NODE = {"id": "uw:jane", "description_model": "legacy_dynamodb"}
EVIDENCE = [
    {"id": 1, "kind": "affiliation", "text": "Affiliated with Test University."},
    {"id": 2, "kind": "paper", "text": 'Authored "Graphs at Scale" (2024).'},
]
GEN_AT = "2026-06-24T00:00:00+00:00"
MODEL = "google/gemini-2.5-flash-lite"


def _desc(evidence=(1, 2), confidence=0.9, summary="Studies graphs.") -> NodeDescription:
    return NodeDescription(summary=summary, evidence=tuple(evidence), confidence=confidence)


def _build(node, desc, evidence=EVIDENCE, min_confidence=0.5):
    return evaluate_description(
        node, desc, evidence, generated_at=GEN_AT, model=MODEL, min_confidence=min_confidence
    )


def test_accepts_grounded_description() -> None:
    update, reason = _build(NODE, _desc())
    assert reason is None
    assert update == {
        "node_id": NODE["id"],
        "ai_description": "Studies graphs.",
        "description_model": MODEL,
        "description_generated_at": GEN_AT,
        "description_evidence": EVIDENCE,  # the cited items, in id order
    }


def test_persists_only_cited_evidence() -> None:
    update, _ = _build(NODE, _desc(evidence=(2,)))
    assert [e["id"] for e in update["description_evidence"]] == [2]


def test_preserves_legacy_description() -> None:
    update, reason = _build(LEGACY_NODE, _desc())
    assert update is None and reason == "legacy-preserve"


def test_rejects_low_confidence() -> None:
    update, reason = _build(NODE, _desc(confidence=0.3), min_confidence=0.5)
    assert update is None and reason == "low-confidence"


def test_rejects_uncited_description() -> None:
    update, reason = _build(NODE, _desc(evidence=()))
    assert update is None and reason == "ungrounded"


def test_rejects_hallucinated_citation() -> None:
    # id 99 was never shown to the model -> the whole description is ungrounded.
    update, reason = _build(NODE, _desc(evidence=(1, 99)))
    assert update is None and reason == "ungrounded"


def test_convenience_wrapper_matches() -> None:
    update = build_description_update(
        NODE, _desc(), EVIDENCE, generated_at=GEN_AT, model=MODEL
    )
    assert update is not None and update["node_id"] == NODE["id"]
