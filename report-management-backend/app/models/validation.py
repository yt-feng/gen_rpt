import uuid
from datetime import datetime
from sqlalchemy import (
    String,
    Integer,
    Float,
    DateTime,
    ForeignKey,
    Index,
    Boolean,
    func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base, UUIDMixin, TimestampMixin, JSONVariant

class ValidationPolicy(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "validation_policies"

    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    min_authority: Mapped[float] = mapped_column(Float, default=0.5)
    min_freshness: Mapped[float] = mapped_column(Float, default=0.5)
    min_confidence: Mapped[float] = mapped_column(Float, default=0.5)
    max_duplicate_ratio: Mapped[float] = mapped_column(Float, default=0.3)
    min_sources: Mapped[int] = mapped_column(Integer, default=2)
    conflict_threshold: Mapped[float] = mapped_column(Float, default=0.5)
    knowledge_quality_threshold: Mapped[float] = mapped_column(Float, default=0.5)
    rules: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)

    __table_args__ = (
        Index("ix_validation_policies_name", "name"),
        Index("ix_validation_policies_is_active", "is_active"),
    )


class ValidationReport(Base, UUIDMixin):
    __tablename__ = "validation_reports"

    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("retrieval_sessions.id", ondelete="CASCADE"), nullable=True)
    knowledge_snapshot: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    retrieved_sources: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    validation_summary: Mapped[str | None] = mapped_column(String, nullable=True)
    authority_scores: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    freshness_scores: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    confidence_scores: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    conflicts: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    duplicate_analysis: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    evidence_completeness: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    unsupported_evidence: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    recommendations: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    r2_path: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("ix_validation_reports_session_id", "session_id"),
    )


class ValidationHistory(Base, UUIDMixin):
    __tablename__ = "validation_history"

    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("retrieval_sessions.id", ondelete="CASCADE"), nullable=True)
    validation_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    knowledge_version: Mapped[str] = mapped_column(String, nullable=False)
    validation_policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("validation_policies.id", ondelete="SET NULL"), nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, default=1.0)
    conflict_count: Mapped[int] = mapped_column(Integer, default=0)
    freshness_score: Mapped[float] = mapped_column(Float, default=1.0)
    details: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("ix_validation_history_session_id", "session_id"),
        Index("ix_validation_history_validation_run_id", "validation_run_id"),
    )


class ValidationAuditLog(Base, UUIDMixin):
    __tablename__ = "validation_audit_logs"

    validator_version: Mapped[str] = mapped_column(String, nullable=False)
    execution_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    knowledge_snapshot: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    retrieved_chunks: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    validation_rules: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    results: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    warnings: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    errors: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("ix_validation_audit_logs_user_id", "user_id"),
    )
# Custom Validation Audit Log verification touch marker