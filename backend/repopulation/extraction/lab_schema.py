"""Strict lab-extraction schema + validator  [Cursor task P3-T02].

Implement per SCRAPING.md §2: the JSON schema the LLM must satisfy, the LabExtraction dataclass, and
`validate(obj) -> LabExtraction | None` (None on off-schema / missing-required). `validate` is the
injection backstop — the model's JSON is validated STRUCTURALLY before anything can affect the graph;
anything not matching the schema is rejected (-> caller quarantines).

PURE: no network/DB/LLM/clock. Forbidden: importing clients/* or any HTTP lib.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LabExtraction:
    lab_name: str
    pi: str | None
    members: tuple[str, ...]
    research_areas: tuple[str, ...]
    self_description: str | None
    source_anchor: str | None
    confidence: float


# Strict JSON schema the LLM response must satisfy (OpenRouter response_format). `additionalProperties:
# false` is the structural injection backstop: any control field the model is tricked into emitting
# (e.g. a "tool_call") makes the whole object off-schema and is rejected. Required = the fields the
# graph needs to ground a lab claim; pi/self_description/source_anchor are optional and nullable.
LAB_JSON_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lab_name", "members", "research_areas", "confidence"],
    "properties": {
        "lab_name": {"type": "string"},
        "pi": {"type": ["string", "null"]},
        "members": {"type": "array", "items": {"type": "string"}},
        "research_areas": {"type": "array", "items": {"type": "string"}},
        "self_description": {"type": ["string", "null"]},
        "source_anchor": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

# Derived from the schema so the validator can never drift from the declared contract.
_ALLOWED_KEYS = frozenset(LAB_JSON_SCHEMA["properties"])
_REQUIRED_KEYS = frozenset(LAB_JSON_SCHEMA["required"])


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_opt_str(value: object) -> bool:
    return value is None or isinstance(value, str)


def validate(obj: dict) -> LabExtraction | None:
    """Return a LabExtraction if `obj` matches the schema (types + required fields), else None.

    Structural-only: no field is invented or coerced beyond int->float on confidence. Anything
    off-schema (wrong types, missing required keys, or EXTRA/control keys) returns None so the
    caller can quarantine it. This is the injection backstop on the model's output.
    """
    if not isinstance(obj, dict):
        return None
    if set(obj) - _ALLOWED_KEYS:  # extra / control fields -> reject
        return None
    if not _REQUIRED_KEYS.issubset(obj):  # missing required -> reject
        return None

    lab_name = obj["lab_name"]
    members = obj["members"]
    research_areas = obj["research_areas"]
    confidence = obj["confidence"]
    pi = obj.get("pi")
    self_description = obj.get("self_description")
    source_anchor = obj.get("source_anchor")

    if not _is_str(lab_name):
        return None
    if not _is_str_list(members):
        return None
    if not _is_str_list(research_areas):
        return None
    if not (_is_opt_str(pi) and _is_opt_str(self_description) and _is_opt_str(source_anchor)):
        return None

    # confidence: a real number (bool is not a number here) coerced to float and bounded to 0..1.
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    confidence = float(confidence)
    if not 0.0 <= confidence <= 1.0:
        return None

    return LabExtraction(
        lab_name=lab_name,
        pi=pi,
        members=tuple(members),
        research_areas=tuple(research_areas),
        self_description=self_description,
        source_anchor=source_anchor,
        confidence=confidence,
    )
