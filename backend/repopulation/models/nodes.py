"""Node + relevance + embedding ORM models  [Cursor task P1-T01].

Mirror `repop.node`, `repop.relevance`, `repop.embedding`, and `repop.repopulation_run` from
migrations/0001_initial.sql EXACTLY (column names, nullability, CHECK constraints, PKs, and the
partial-unique dedup indexes on orcid/openalex_id/ror). SQLAlchemy 2.0 typed declarative style.
No engine/session/connection code and no DDL execution here — models only.

`Base` is defined here (carrying the `repop` schema on its MetaData) and is imported by the
sibling model files (edges.py, provenance.py) so every table shares one registry/metadata. See
SCHEMA.md for field meanings.
"""

from __future__ import annotations

import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all repop models; every table lives in the `repop` schema."""

    metadata = MetaData(schema="repop")


class RepopulationRun(Base):
    """Mirror of `repop.repopulation_run` — scopes relevance + provenance to one seed/run."""

    __tablename__ = "repopulation_run"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    seed: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','succeeded','failed','quarantine')",
            name="repopulation_run_status_check",
        ),
    )


class Node(Base):
    """Mirror of `repop.node` — unified table for all node kinds.

    Rendered, type-specific fields live in `attributes` (jsonb); first-class columns are reserved
    for identity/dedup keys and the grounded AI description.
    """

    __tablename__ = "node"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    val: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # identity / dedup keys (dedup order: ORCID -> OpenAlex -> ROR -> normalized name)
    orcid: Mapped[str | None] = mapped_column(Text)
    openalex_id: Mapped[str | None] = mapped_column(Text)
    ror: Mapped[str | None] = mapped_column(Text)
    normalized_name: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # grounded AI description (legacy `about` backfilled as legacy_dynamodb)
    ai_description: Mapped[str | None] = mapped_column(Text)
    description_model: Mapped[str | None] = mapped_column(Text)
    description_generated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    description_evidence: Mapped[Any | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Double)
    source_record_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("repop.source_record.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('researcher','lab','institution','department','topic','venue','paper')",
            name="node_kind_check",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="node_confidence_check"
        ),
        # Partial-unique dedup indexes (only enforce where the key is present).
        Index(
            "node_orcid_uq",
            "orcid",
            unique=True,
            postgresql_where=text("orcid IS NOT NULL"),
        ),
        Index(
            "node_openalex_uq",
            "openalex_id",
            unique=True,
            postgresql_where=text("openalex_id IS NOT NULL"),
        ),
        Index(
            "node_ror_uq",
            "ror",
            unique=True,
            postgresql_where=text("ror IS NOT NULL"),
        ),
        Index("node_kind_idx", "kind"),
        Index("node_norm_name_idx", "normalized_name"),
    )


class Relevance(Base):
    """Mirror of `repop.relevance` — query-scoped score per (node, run)."""

    __tablename__ = "relevance"

    node_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("repop.node.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repop.repopulation_run.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    score: Mapped[float] = mapped_column(Double, nullable=False)
    components: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("relevance_run_idx", "run_id"),)


class Embedding(Base):
    """Mirror of `repop.embedding` — pgvector retrieval store (populated in Phase 2)."""

    __tablename__ = "embedding"

    node_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("repop.node.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    model: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    embedding: Mapped[Any | None] = mapped_column(Vector(1536))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
