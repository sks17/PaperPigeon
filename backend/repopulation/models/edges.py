"""Edge ORM model  [Cursor task P1-T01].

Mirror `repop.edge` from migrations/0001_initial.sql EXACTLY: src_id/dst_id FKs to repop.node
(ON DELETE CASCADE), the `type` CHECK over the 13 rich edge types, weight (default 1.0), directed
(default true), attributes jsonb, confidence (0..1 CHECK), source_record_id FK, and the
UNIQUE (src_id, dst_id, type) idempotency constraint. SQLAlchemy 2.0 typed declarative. Models
only — no connections, no DDL.

`Base` is imported from nodes.py so every repop table shares one registry/metadata.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.repopulation.models.nodes import Base


class Edge(Base):
    """Mirror of `repop.edge` — typed, directed, weighted, provenance-bearing relationship."""

    __tablename__ = "edge"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    src_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("repop.node.id", ondelete="CASCADE"),
        nullable=False,
    )
    dst_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("repop.node.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(
        Double, nullable=False, server_default=text("1.0")
    )
    directed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    confidence: Mapped[float | None] = mapped_column(Double)
    source_record_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("repop.source_record.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('AUTHORED','MEMBER_OF','AFFILIATED_WITH','PART_OF',"
            "'COAUTHORED_WITH','ADVISES','ADVISED_BY','COLLABORATES_WITH',"
            "'WORKS_ON','FOCUSES_ON','CITES','ALUMNUS_OF','SIMILAR_TO')",
            name="edge_type_check",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="edge_confidence_check"
        ),
        # idempotency: re-running a seed must not create duplicate edges
        UniqueConstraint("src_id", "dst_id", "type", name="edge_uq"),
        Index("edge_src_idx", "src_id"),
        Index("edge_dst_idx", "dst_id"),
        Index("edge_type_idx", "type"),
    )
