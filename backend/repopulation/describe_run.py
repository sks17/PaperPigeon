"""Grounded RAG description orchestration (Phase 4, main-thread integration).

For each researcher node in a repopulation run, gather evidence over Postgres + pgvector
(`descriptions.retrieve`), prompt the LLM for a grounded description (`descriptions.prompt` +
`clients.llm`), structurally validate it (`extraction.description_schema` — the injection backstop),
gate it for grounding/legacy-preservation (`descriptions.build_rows`), and write the survivors onto
their nodes (`loader.apply_description_updates`). Rejected descriptions are quarantined for audit,
never dropped silently (AGENTS.md constraint 4: quarantine-don't-crash).

Additive + isolated: only the run's own (non-legacy) nodes are described, so the published legacy
graph's `about` text is preserved and the default served graph is unchanged. Idempotent: nodes
already described by this `model` are skipped, so re-running a seed is a no-op.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.repopulation.clients.llm import LlmError
from backend.repopulation.descriptions.build_rows import LEGACY_DESCRIPTION_MODEL, evaluate_description
from backend.repopulation.descriptions.prompt import build_description_prompt
from backend.repopulation.descriptions.retrieve import gather_evidence
from backend.repopulation.extraction.description_schema import validate
from backend.repopulation.loader import apply_description_updates, get_published_run_id
from backend.repopulation.models.membership import Quarantine, RunNode
from backend.repopulation.models.nodes import Node

DEFAULT_KINDS = ("researcher",)


def describe_run(
    session: Session,
    run_id: int,
    *,
    llm,
    generated_at: str,
    model: str | None = None,
    min_confidence: float = 0.5,
    kinds: tuple[str, ...] = DEFAULT_KINDS,
    neighbours: int = 5,
    embedding_model: str | None = None,
    limit: int | None = None,
) -> dict:
    """Generate + persist grounded descriptions for `run_id`'s nodes. `model` defaults to the LLM
    client's model id (recorded as `description_model`). `embedding_model` enables pgvector
    "related-researcher" evidence; omit it (or run without embeddings) to ground on stored facts
    only. `limit` caps how many nodes are described (cost guard). Returns run counts."""
    model = model or getattr(llm, "model", "unknown")

    nodes = _describable_nodes(session, run_id, kinds, model, limit)

    updates: list[dict] = []
    quarantined: list[tuple[str, str]] = []  # (node_id, reason)
    described = skipped_no_evidence = 0

    for node in nodes:
        evidence = gather_evidence(
            session, node, run_id, k=neighbours, model=embedding_model
        )
        if not evidence:
            skipped_no_evidence += 1
            quarantined.append((node.id, "no-evidence"))
            continue

        system, user = build_description_prompt(
            {"name": node.name, "kind": node.kind}, evidence
        )
        try:
            raw = llm.complete_json(system, user)
        except LlmError as exc:
            quarantined.append((node.id, f"llm-error:{exc}"))
            continue

        description = validate(raw)
        if description is None:
            quarantined.append((node.id, "invalid-extraction"))
            continue

        update, reason = evaluate_description(
            _node_view(node), description, evidence,
            generated_at=generated_at, model=model, min_confidence=min_confidence,
        )
        if update is None:
            quarantined.append((node.id, reason or "rejected"))
            continue

        updates.append(update)
        described += 1

    updated = apply_description_updates(session, updates)

    for node_id, reason in quarantined:
        session.add(
            Quarantine(
                run_id=run_id,
                kind="description",
                payload={"node_id": node_id},
                reason=reason,
            )
        )
    session.commit()

    return {
        "run_id": run_id,
        "candidates": len(nodes),
        "described": described,
        "updated": updated,
        "quarantined": len(quarantined),
        "no_evidence": skipped_no_evidence,
        "model": model,
    }


def _describable_nodes(
    session: Session, run_id: int, kinds: tuple[str, ...], model: str, limit: int | None
) -> list[Node]:
    """Run-member nodes of the requested kinds that still need a description by `model`.

    Excludes, for snapshot isolation: nodes that also belong to the PUBLISHED run (a repop run can
    reuse legacy lab ids via build_lab_rows, so a legacy/published lab can be in this run's
    membership — we must never re-describe a node the default graph serves); legacy DynamoDB
    descriptions (preserve); and nodes already described by this model (idempotency)."""
    q = (
        select(Node)
        .join(RunNode, RunNode.node_id == Node.id)
        .where(
            RunNode.run_id == run_id,
            Node.kind.in_(kinds),
            Node.description_model.is_distinct_from(LEGACY_DESCRIPTION_MODEL),
            Node.description_model.is_distinct_from(model),
        )
        .order_by(Node.id)
    )

    published = get_published_run_id(session)
    if published is not None and published != run_id:
        published_nodes = select(RunNode.node_id).where(RunNode.run_id == published)
        q = q.where(Node.id.not_in(published_nodes))

    if limit is not None:
        q = q.limit(limit)
    return list(session.scalars(q).all())


def _node_view(node: Node) -> dict:
    """The minimal dict the pure gate (`evaluate_description`) reads from a Node ORM row."""
    return {"id": node.id, "description_model": node.description_model}
