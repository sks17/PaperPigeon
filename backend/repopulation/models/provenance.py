"""SourceRecord ORM model  [Cursor task P1-T01].

Mirror `repop.source_record` from migrations/0001_initial.sql EXACTLY: the `source` CHECK
(openalex|crossref|arxiv|pubmed|scrape|ai|legacy_cache), source_url, retrieved_at, confidence
(0..1 CHECK), evidence, run_id FK, raw_s3_key. SQLAlchemy 2.0 typed declarative. Models only —
no connections, no DDL.

`Base` is imported from nodes.py so every repop table shares one registry/metadata.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.repopulation.models.nodes import Base


class SourceRecord(Base):
    """Mirror of `repop.source_record` — the provenance spine; every node/edge points to one."""

    __tablename__ = "source_record"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    confidence: Mapped[float | None] = mapped_column(Double)
    # affiliation string / API field / scraped selector
    evidence: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("repop.repopulation_run.id", ondelete="SET NULL"),
    )
    # replayability: raw payload stored in S3 before transform
    raw_s3_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "source IN ('openalex','crossref','arxiv','pubmed','scrape','ai','legacy_cache')",
            name="source_record_source_check",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="source_record_confidence_check",
        ),
    )
