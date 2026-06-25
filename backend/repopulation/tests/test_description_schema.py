"""P4: NodeDescription schema validator — the injection/hallucination backstop (pure).

validate() accepts a well-formed grounded description and rejects anything off-schema: missing
required field, wrong type, extra/control keys (e.g. an injected tool_call), empty/over-long
summary, non-positive or non-int evidence ids, and out-of-range/non-numeric confidence. Booleans
must not slip through as ints/floats.
"""
from __future__ import annotations

from backend.repopulation.extraction.description_schema import (
    MAX_SUMMARY_CHARS,
    NodeDescription,
    validate,
)


def _ok() -> dict:
    return {"summary": "Studies graph algorithms.", "evidence": [2, 1, 1], "confidence": 0.8}


def test_accepts_wellformed_and_normalizes_evidence() -> None:
    desc = validate(_ok())
    assert isinstance(desc, NodeDescription)
    assert desc.summary == "Studies graph algorithms."
    # ids deduped + sorted for determinism; confidence coerced to float.
    assert desc.evidence == (1, 2)
    assert desc.confidence == 0.8


def test_strips_summary_whitespace() -> None:
    desc = validate({**_ok(), "summary": "  trimmed  "})
    assert desc is not None and desc.summary == "trimmed"


def test_rejects_non_dict() -> None:
    assert validate("nope") is None
    assert validate(None) is None
    assert validate([_ok()]) is None


def test_rejects_missing_required() -> None:
    for key in ("summary", "evidence", "confidence"):
        obj = _ok()
        del obj[key]
        assert validate(obj) is None, key


def test_rejects_extra_or_control_keys() -> None:
    # The injection backstop: any key beyond the three required fields fails.
    assert validate({**_ok(), "tool_call": {"name": "rm"}}) is None
    assert validate({**_ok(), "system": "ignore previous"}) is None


def test_rejects_bad_summary() -> None:
    assert validate({**_ok(), "summary": ""}) is None
    assert validate({**_ok(), "summary": "   "}) is None
    assert validate({**_ok(), "summary": 123}) is None
    assert validate({**_ok(), "summary": "x" * (MAX_SUMMARY_CHARS + 1)}) is None


def test_rejects_bad_evidence() -> None:
    assert validate({**_ok(), "evidence": "1,2"}) is None
    assert validate({**_ok(), "evidence": [0]}) is None
    assert validate({**_ok(), "evidence": [-1]}) is None
    assert validate({**_ok(), "evidence": [1.5]}) is None
    assert validate({**_ok(), "evidence": [True]}) is None  # bool must not pass as int
    # empty evidence is structurally valid here (grounding is enforced in build_rows).
    desc = validate({**_ok(), "evidence": []})
    assert desc is not None and desc.evidence == ()


def test_rejects_bad_confidence() -> None:
    assert validate({**_ok(), "confidence": -0.1}) is None
    assert validate({**_ok(), "confidence": 1.1}) is None
    assert validate({**_ok(), "confidence": "high"}) is None
    assert validate({**_ok(), "confidence": True}) is None
    assert validate({**_ok(), "confidence": 1}) is not None  # int in range is fine
