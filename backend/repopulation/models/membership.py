"""Run-membership + app-state ORM models (migration 0002).

run_node / run_edge record which nodes/edges constitute a repopulation run's snapshot, so the
served graph can default to the published run and treat new runs as additive/invisible until
published. app_state holds simple pointers like `published_run_id`. Mirrors 0002_run_membership.sql.
"""
from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.repopulation.models.nodes import Base

PUBLISHED_RUN_KEY = "published_run_id"


class RunNode(Base):
    __tablename__ = "run_node"

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repop.repopulation_run.id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[str] = mapped_column(
        Text, ForeignKey("repop.node.id", ondelete="CASCADE"), primary_key=True
    )


class RunEdge(Base):
    __tablename__ = "run_edge"

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repop.repopulation_run.id", ondelete="CASCADE"), primary_key=True
    )
    edge_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repop.edge.id", ondelete="CASCADE"), primary_key=True
    )


class AppState(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)


class Quarantine(Base):
    """Mirror of `repop.quarantine` (migration 0003) — audit of dropped low-confidence records."""

    __tablename__ = "quarantine"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("repop.repopulation_run.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
