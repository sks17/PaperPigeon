"""Tests for the strict lab-extraction validator (P3-T05).

The validator is the injection backstop for LLM lab extraction: only structurally valid data
may become a LabExtraction. These tests are pure inline fixtures; no network/DB/LLM calls.
"""
from __future__ import annotations

from backend.repopulation.extraction.lab_schema import (
    LAB_JSON_SCHEMA,
    LabExtraction,
    validate,
)


def _valid_payload(**overrides) -> dict:
    payload = {
        "lab_name": "Systems Research Lab",
        "pi": "Ada Lovelace",
        "members": ["Grace Hopper", "Katherine Johnson"],
        "research_areas": ["distributed systems", "programming languages"],
        "self_description": "We study reliable distributed systems.",
        "source_anchor": "h1#systems-research-lab",
        "confidence": 0.82,
    }
    payload.update(overrides)
    return payload


def test_schema_is_strict_and_has_expected_required_fields() -> None:
    assert LAB_JSON_SCHEMA["type"] == "object"
    assert LAB_JSON_SCHEMA["additionalProperties"] is False
    assert set(LAB_JSON_SCHEMA["required"]) == {
        "lab_name",
        "members",
        "research_areas",
        "confidence",
    }


def test_validate_accepts_well_formed_extraction() -> None:
    extraction = validate(_valid_payload())

    assert extraction == LabExtraction(
        lab_name="Systems Research Lab",
        pi="Ada Lovelace",
        members=("Grace Hopper", "Katherine Johnson"),
        research_areas=("distributed systems", "programming languages"),
        self_description="We study reliable distributed systems.",
        source_anchor="h1#systems-research-lab",
        confidence=0.82,
    )


def test_validate_accepts_missing_optional_fields_as_none() -> None:
    extraction = validate(
        {
            "lab_name": "Systems Research Lab",
            "members": ["Grace Hopper"],
            "research_areas": ["distributed systems"],
            "confidence": 1,
        }
    )

    assert extraction == LabExtraction(
        lab_name="Systems Research Lab",
        pi=None,
        members=("Grace Hopper",),
        research_areas=("distributed systems",),
        self_description=None,
        source_anchor=None,
        confidence=1.0,
    )


def test_validate_rejects_missing_required_field() -> None:
    payload = _valid_payload()
    del payload["lab_name"]

    assert validate(payload) is None


def test_validate_rejects_wrong_type() -> None:
    assert validate(_valid_payload(members="Grace Hopper")) is None
    assert validate(_valid_payload(research_areas=[42])) is None
    assert validate(_valid_payload(confidence="0.8")) is None


def test_validate_rejects_extra_or_control_keys() -> None:
    assert validate(_valid_payload(tool_call={"name": "fetch_url"})) is None
    assert validate(_valid_payload(ignore_previous_instructions=True)) is None


def test_validate_rejects_confidence_outside_zero_to_one() -> None:
    assert validate(_valid_payload(confidence=-0.01)) is None
    assert validate(_valid_payload(confidence=1.01)) is None
