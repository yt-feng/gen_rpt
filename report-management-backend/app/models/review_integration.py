import uuid
from datetime import datetime
from sqlalchemy import String, ForeignKey, DateTime, Integer, Float, Boolean, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base, UUIDMixin, JSONVariant

class ReviewSnapshot(Base, UUIDMixin):
    __tablename__ = "review_snapshots"

    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    knowledge_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_snapshots.id", ondelete="SET NULL"), nullable=True)
    validation_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("validation_reports.id", ondelete="SET NULL"), nullable=True)
    ai_review_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("ai_reviews.id", ondelete="SET NULL"), nullable=True)
    human_review_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("human_reviews.id", ondelete="SET NULL"), nullable=True)
    evidence_attribution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("evidence_attributions.id", ondelete="SET NULL"), nullable=True)
    
    review_results: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    r2_path: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    # Relationships (Optional, but good practice for SQLAlchemy eager loading)
    version = relationship("DocumentVersion", foreign_keys=[version_id])
    reviewer = relationship("User", foreign_keys=[reviewer_id])

    __table_args__ = (
        Index("ix_review_snapshots_version_id", "version_id"),
        Index("ix_review_snapshots_knowledge_snapshot_id", "knowledge_snapshot_id"),
    )


class ReviewAnalytics(Base, UUIDMixin):
    __tablename__ = "review_analytics"

    review_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("review_snapshots.id", ondelete="CASCADE"), nullable=True)
    evidence_usage: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    citation_quality: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, default=1.0)
    unsupported_claims_count: Mapped[int] = mapped_column(Integer, default=0)
    conflicts_count: Mapped[int] = mapped_column(Integer, default=0)
    review_duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("ix_review_analytics_snapshot_id", "review_snapshot_id"),
    )
