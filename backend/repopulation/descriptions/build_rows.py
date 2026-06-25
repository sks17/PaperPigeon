"""Turn a validated `NodeDescription` into a persistable node-description update (Phase 4) — pure.

`build_description_update` is the grounding gate ("no evidence -> no claim", AGENTS.md constraint 5)
and the legacy-preservation gate ("preserve existing", build sequence step 4). Given a node, the
model's validated `NodeDescription`, and the exact evidence list shown to the model, it returns the
update dict the loader applies — or ``None`` (with `quarantine_reason` available via the companion
helper) when the description must be rejected:

  - the node already carries a legacy DynamoDB `about` (description_model == 'legacy_dynamodb')
    -> never overwrite it;
  - the model's self-confidence is below `min_confidence`;
  - the description cites NO evidence, or cites an id that was not shown to it (a hallucinated
    citation) -> the whole description is ungrounded and dropped.

The persisted `description_evidence` is exactly the cited evidence items, so every stored
description carries its grounding for audit. Pure: no DB / network / clock (caller passes
`generated_at`). Deterministic + idempotent.
"""
from __future__ import annotations

from backend.repopulation.extraction.description_schema import NodeDescription

LEGACY_DESCRIPTION_MODEL = "legacy_dynamodb"


def is_legacy(node: dict) -> bool:
    """True when the node's description is the preserved legacy DynamoDB `about` (never overwrite)."""
    return node.get("description_model") == LEGACY_DESCRIPTION_MODEL


def evaluate_description(
    node: dict,
    description: NodeDescription,
    evidence: list[dict],
    *,
    generated_at: str,
    model: str,
    min_confidence: float = 0.5,
) -> tuple[dict | None, str | None]:
    """Core gate. Returns (update_or_None, reason_or_None).

    On accept: (update, None). On reject: (None, reason) where reason is one of
    'legacy-preserve' | 'low-confidence' | 'ungrounded'. See module docstring.
    """
    if is_legacy(node):
        return None, "legacy-preserve"

    if description.confidence < min_confidence:
        return None, "low-confidence"

    by_id = {item["id"]: item for item in evidence}
    # Every cited id must resolve to evidence actually shown to the model; a dangling citation is a
    # hallucination signal, so the whole description is rejected (not silently trimmed).
    if not description.evidence or any(cid not in by_id for cid in description.evidence):
        return None, "ungrounded"

    cited = [by_id[cid] for cid in description.evidence]
    update = {
        "node_id": node["id"],
        "ai_description": description.summary,
        "description_model": model,
        "description_generated_at": generated_at,
        "description_evidence": cited,
    }
    return update, None


def build_description_update(
    node: dict,
    description: NodeDescription,
    evidence: list[dict],
    *,
    generated_at: str,
    model: str,
    min_confidence: float = 0.5,
) -> dict | None:
    """Convenience wrapper returning just the update dict (or None). See `evaluate_description`."""
    update, _ = evaluate_description(
        node, description, evidence,
        generated_at=generated_at, model=model, min_confidence=min_confidence,
    )
    return update
