"""Promote a run's grounded descriptions onto the published graph (Phase 4, deliberate/opt-in).

describe_run writes descriptions onto a repopulation run's OWN nodes (openalex ids), leaving the
published legacy graph untouched. `promote_descriptions` is the explicit step that enriches the
published researchers with those grounded bios, reconciling by normalized name (legacy nodes carry
name-based ids, so we match on the name, not the id).

Safety: this is the ONE place that intentionally writes to published nodes, so it is conservative by
default — it FILLS only researchers that have no description yet and never overwrites an existing
`about` (the preserved legacy DynamoDB text) unless `overwrite=True` is passed deliberately. Promoted
descriptions are tagged `promoted:<model>` so the step is idempotent (a re-promote sees a non-empty
description and skips it). Main-thread (DB writes); not pure.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.repopulation.descriptions.build_rows import LEGACY_DESCRIPTION_MODEL
from backend.repopulation.loader import get_published_run_id
from backend.repopulation.models.membership import RunNode
from backend.repopulation.models.nodes import Node

PROMOTED_PREFIX = "promoted:"


def _normalize(name: str | None) -> str | None:
    if name is None:
        return None
    collapsed = re.sub(r"\s+", " ", name).strip().lower()
    return collapsed or None


def promote_descriptions(
    session: Session,
    from_run_id: int,
    *,
    to_run_id: int | None = None,
    overwrite: bool = False,
    kind: str = "researcher",
) -> dict:
    """Copy grounded descriptions from `from_run_id`'s nodes onto the target run's nodes (default:
    the published run), matched by normalized name + kind. Returns
    {matched, promoted, skipped_existing, no_match}. See module docstring for the safety contract."""
    target_run = to_run_id if to_run_id is not None else get_published_run_id(session)
    if target_run is None or target_run == from_run_id:
        return {"matched": 0, "promoted": 0, "skipped_existing": 0, "no_match": 0}

    # Source: nodes described by an actual model (not legacy, not a prior promote) in the source run.
    sources = session.scalars(
        select(Node)
        .join(RunNode, RunNode.node_id == Node.id)
        .where(
            RunNode.run_id == from_run_id,
            Node.kind == kind,
            Node.ai_description.isnot(None),
            Node.description_model.isnot(None),
            Node.description_model != LEGACY_DESCRIPTION_MODEL,
        )
    ).all()
    by_name = {}
    for node in sources:
        key = _normalize(node.name)
        if key is not None:
            by_name.setdefault(key, node)  # first wins; deterministic over the run's node set

    targets = session.scalars(
        select(Node)
        .join(RunNode, RunNode.node_id == Node.id)
        .where(RunNode.run_id == target_run, Node.kind == kind)
    ).all()

    matched = promoted = skipped_existing = no_match = 0
    for target in targets:
        source = by_name.get(_normalize(target.name))
        if source is None:
            no_match += 1
            continue
        matched += 1
        has_description = bool((target.ai_description or "").strip())
        if has_description and not overwrite:
            skipped_existing += 1
            continue

        model = source.description_model or ""
        target.ai_description = source.ai_description
        target.description_model = (
            model if model.startswith(PROMOTED_PREFIX) else f"{PROMOTED_PREFIX}{model}"
        )
        target.description_generated_at = source.description_generated_at
        target.description_evidence = source.description_evidence
        promoted += 1

    session.commit()
    return {
        "matched": matched,
        "promoted": promoted,
        "skipped_existing": skipped_existing,
        "no_match": no_match,
    }
