"""Reconcile extracted labs into ImportRows + quarantine  [Cursor task P3-T03].

Implement `build_lab_rows(...)` per SCRAPING.md §3: map validated LabExtractions + the institution +
the OpenAlex researcher set + the legacy lab list into {accepted: ImportRows, quarantined: [...]}.
Reconcile members to researcher_ids (normalized-name), merge labs with legacy lab_ids, dedupe, and
quarantine low-confidence / ungrounded / unmatched records ("quarantine, don't crash";
"no evidence -> no claim"). Emit lab/department nodes + MEMBER_OF/PART_OF/FOCUSES_ON edges, each with
a source_record_key (source='scrape').

PURE: no HTTP/DB/clock/LLM. Deterministic + idempotent (stable lab ids; edge identity (src,dst,type)).
Forbidden: importing clients/* or any HTTP lib; touching the loader/serializer.
"""
from __future__ import annotations

import re
from typing import Any

MEMBER_OF = "MEMBER_OF"
PART_OF = "PART_OF"
FOCUSES_ON = "FOCUSES_ON"
SCRAPE_DESC_MODEL = "scrape"
SCRAPE_CONFIDENCE_DEFAULT = 1.0
NODE_VAL = {"lab": 2, "institution": 3, "topic": 4, "department": 6}


def normalize(name: str | None) -> str | None:
    if name is None:
        return None
    collapsed = re.sub(r"\s+", " ", name).strip().lower()
    return collapsed or None


def build_lab_rows(
    extractions: list[dict],
    institution: dict,
    researcher_set: list[dict],
    legacy_labs: list[tuple],
    run_key: str,
    source_keys: dict,
    *,
    min_confidence: float = 0.5,
) -> dict:
    """Returns {"accepted": ImportRows (SCHEMA.md §1), "quarantined": [{kind, payload, reason}]}.
    See SCRAPING.md §3 for the full mapping + rules."""
    scrape_key_base = source_keys.get("scrape", "scrape")
    institution_id = institution["id"]
    institution_name = institution.get("name") or institution_id

    accepted = {
        "runs": [{"key": run_key, "seed": {"source": "scrape"}, "status": "running"}],
        "source_records": [],
        "nodes": [],
        "edges": [],
        "relevance": [],
    }
    quarantined: list[dict] = []

    researchers_by_normalized = _researchers_by_normalized_name(researcher_set)
    legacy_lab_ids = {
        normalize(display_name): lab_id
        for lab_id, display_name in legacy_labs
        if normalize(display_name) is not None
    }

    seen_source_keys: set[str] = set()
    seen_node_ids: set[str] = set()
    seen_edge_keys: set[tuple[str, str, str]] = set()
    def add_source_record(row: dict) -> None:
        if row["key"] in seen_source_keys:
            return
        seen_source_keys.add(row["key"])
        accepted["source_records"].append(row)

    def add_node(row: dict) -> None:
        if row["id"] in seen_node_ids:
            return
        seen_node_ids.add(row["id"])
        accepted["nodes"].append(row)

    def add_edge(row: dict) -> None:
        key = (row["src_id"], row["dst_id"], row["type"])
        if key in seen_edge_keys:
            return
        seen_edge_keys.add(key)
        accepted["edges"].append(row)

    for index, item in enumerate(extractions):
        extraction = item.get("extraction")
        lab_name = _field(extraction, "lab_name")
        lab_norm = normalize(lab_name)
        source_anchor = _first_present(
            item.get("anchor"),
            _field(extraction, "source_anchor"),
        )
        confidence = _confidence(_field(extraction, "confidence"))
        payload = _quarantine_payload(item, extraction)

        if lab_norm is None:
            quarantined.append({"kind": "lab", "payload": payload, "reason": "missing-lab-name"})
            continue
        if confidence < min_confidence:
            quarantined.append({"kind": "lab", "payload": payload, "reason": "low-confidence"})
            continue
        if not source_anchor:
            quarantined.append({"kind": "lab", "payload": payload, "reason": "missing-source-anchor"})
            continue

        department_name = _department_name(item, extraction)
        department_norm = normalize(department_name)

        source_record_key = _source_record_key(scrape_key_base, index, item)
        add_source_record(
            _source_record_row(
                source_record_key,
                run_key,
                source_url=item.get("source_url"),
                raw_s3_key=item.get("raw_key"),
                evidence=source_anchor,
            )
        )

        matched_researcher_ids: list[str] = []
        for member in _tuple_field(extraction, "members"):
            matched = researchers_by_normalized.get(normalize(member))
            if matched is None:
                quarantined.append(
                    {
                        "kind": "member",
                        "payload": {"member": member, "lab": lab_name},
                        "reason": "unmatched-researcher",
                    }
                )
                continue
            if len(matched) != 1:
                quarantined.append(
                    {
                        "kind": "member",
                        "payload": {"member": member, "lab": lab_name},
                        "reason": "ambiguous-researcher",
                    }
                )
                continue
            researcher_id = matched[0]["id"]
            matched_researcher_ids.append(researcher_id)

        lab_id = legacy_lab_ids.get(lab_norm) or _new_lab_id(institution_id, lab_norm, department_norm)
        research_areas = _tuple_field(extraction, "research_areas")
        add_node(
            _node_row(
                id=lab_id,
                kind="lab",
                name=lab_name,
                normalized_name=lab_norm,
                attributes={
                    "description": _field(extraction, "self_description"),
                    "faculty": _dedupe_preserve_order(matched_researcher_ids),
                    "url": item.get("source_url"),
                    "research_areas": list(research_areas),
                    "pi": _field(extraction, "pi"),
                },
                source_record_key=source_record_key,
                confidence=confidence,
                description_model=SCRAPE_DESC_MODEL,
                ai_description=_field(extraction, "self_description"),
            )
        )

        for researcher_id in _dedupe_preserve_order(matched_researcher_ids):
            add_edge(
                _edge_row(
                    researcher_id,
                    lab_id,
                    MEMBER_OF,
                    source_record_key,
                    weight=1.0,
                    confidence=confidence,
                )
            )

        if department_norm is not None:
            department_id = _department_id(institution_id, department_norm)
            add_node(
                _node_row(
                    id=department_id,
                    kind="department",
                    name=department_name,
                    normalized_name=department_norm,
                    attributes={},
                    source_record_key=source_record_key,
                    confidence=confidence,
                )
            )
            add_edge(
                _edge_row(
                    lab_id,
                    department_id,
                    PART_OF,
                    source_record_key,
                    weight=1.0,
                    confidence=confidence,
                )
            )
            add_node(
                _node_row(
                    id=institution_id,
                    kind="institution",
                    name=institution_name,
                    ror=institution.get("ror"),
                    openalex_id=institution_id,
                    normalized_name=normalize(institution_name),
                    attributes={},
                    source_record_key=source_record_key,
                    confidence=SCRAPE_CONFIDENCE_DEFAULT,
                )
            )
            add_edge(
                _edge_row(
                    department_id,
                    institution_id,
                    PART_OF,
                    source_record_key,
                    weight=1.0,
                    confidence=confidence,
                )
            )

        for area, topic_id in _topic_mappings(item, extraction).items():
            add_edge(
                _edge_row(
                    lab_id,
                    topic_id,
                    FOCUSES_ON,
                    source_record_key,
                    weight=1.0,
                    confidence=confidence,
                    attributes={"research_area": area},
                )
            )

    return {"accepted": accepted, "quarantined": quarantined}


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _tuple_field(obj: Any, name: str) -> tuple:
    value = _field(obj, name)
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return ()


def _first_present(*values: Any) -> Any:
    for value in values:
        if value:
            return value
    return None


def _confidence(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _researchers_by_normalized_name(researcher_set: list[dict]) -> dict[str | None, list[dict]]:
    researchers: dict[str | None, list[dict]] = {}
    for researcher in researcher_set:
        key = researcher.get("normalized_name") or normalize(researcher.get("name"))
        if key is not None:
            researchers.setdefault(key, []).append(researcher)
    return researchers


def _source_record_key(base: str, index: int, item: dict) -> str:
    raw_key = normalize(str(item.get("raw_key"))) if item.get("raw_key") else None
    if raw_key:
        return f"{base}:{_slug(raw_key)}"
    return base if index == 0 else f"{base}:{index + 1}"


def _source_record_row(
    key: str,
    run_key: str,
    *,
    source_url: str | None,
    raw_s3_key: str | None,
    evidence: str,
) -> dict:
    return {
        "key": key,
        "source": "scrape",
        "source_url": source_url,
        "retrieved_at": None,
        "confidence": None,
        "evidence": evidence,
        "run_key": run_key,
        "raw_s3_key": raw_s3_key,
    }


def _node_row(
    *,
    id: str,
    kind: str,
    name: str,
    attributes: dict,
    source_record_key: str,
    confidence: float,
    orcid: str | None = None,
    openalex_id: str | None = None,
    ror: str | None = None,
    normalized_name: str | None = None,
    ai_description: str | None = None,
    description_model: str | None = None,
) -> dict:
    return {
        "id": id,
        "kind": kind,
        "name": name,
        "val": NODE_VAL[kind],
        "orcid": orcid,
        "openalex_id": openalex_id,
        "ror": ror,
        "normalized_name": normalized_name,
        "attributes": attributes,
        "ai_description": ai_description,
        "description_model": description_model,
        "description_generated_at": None,
        "description_evidence": None,
        "confidence": confidence,
        "source_record_key": source_record_key,
    }


def _edge_row(
    src_id: str,
    dst_id: str,
    type_: str,
    source_record_key: str,
    *,
    weight: float,
    confidence: float,
    attributes: dict | None = None,
) -> dict:
    return {
        "src_id": src_id,
        "dst_id": dst_id,
        "type": type_,
        "weight": float(weight),
        "directed": True,
        "attributes": attributes or {},
        "confidence": confidence,
        "source_record_key": source_record_key,
    }


def _new_lab_id(institution_id: str, lab_norm: str, department_norm: str | None) -> str:
    if department_norm:
        return f"lab:{institution_id}:{department_norm}:{lab_norm}"
    return f"lab:{institution_id}:{lab_norm}"


def _department_id(institution_id: str, department_norm: str) -> str:
    return f"department:{institution_id}:{department_norm}"


def _department_name(item: dict, extraction: Any) -> str | None:
    return _first_present(
        item.get("department"),
        item.get("department_name"),
        item.get("parent_department"),
        _field(extraction, "department"),
        _field(extraction, "department_name"),
        _field(extraction, "parent_department"),
    )


def _topic_mappings(item: dict, extraction: Any) -> dict[str, str]:
    mappings = _first_present(
        item.get("topic_ids"),
        item.get("research_area_topic_ids"),
        _field(extraction, "topic_ids"),
        _field(extraction, "research_area_topic_ids"),
    )
    if not isinstance(mappings, dict):
        return {}

    by_area = {normalize(area): area for area in _tuple_field(extraction, "research_areas")}
    result: dict[str, str] = {}
    for area, topic_id in mappings.items():
        area_name = by_area.get(normalize(area), area)
        if area_name and topic_id:
            result[str(area_name)] = str(topic_id)
    return result


def _quarantine_payload(item: dict, extraction: Any) -> dict:
    return {
        "lab": _field(extraction, "lab_name"),
        "source_url": item.get("source_url"),
        "raw_key": item.get("raw_key"),
        "anchor": item.get("anchor") or _field(extraction, "source_anchor"),
        "confidence": _field(extraction, "confidence"),
    }


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")
