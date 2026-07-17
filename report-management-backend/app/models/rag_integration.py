import uuid
from datetime import datetime
from sqlalchemy import String, ForeignKey, DateTime, Integer, Float, Boolean, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base, UUIDMixin, JSONVariant

class KnowledgeSnapshot(Base, UUIDMixin):
    __tablename__ = "knowledge_snapshots"

    knowledge_version: Mapped[str] = mapped_column(String, nullable=False)
    collections_used: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    documents_used: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    chunks_used: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    embedding_version: Mapped[str] = mapped_column(String, nullable=False, default="1.0")
    validation_version: Mapped[str] = mapped_column(String, nullable=False, default="1.0")
    retrieval_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("retrieval_sessions.id", ondelete="SET NULL"), nullable=True)
    configuration: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    r2_path: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("ix_knowledge_snapshots_retrieval_session_id", "retrieval_session_id"),
    )


class EvidenceAttribution(Base, UUIDMixin):
    __tablename__ = "evidence_attributions"

    generation_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("generation_jobs.id", ondelete="CASCADE"), nullable=True)
    section_id: Mapped[str | None] = mapped_column(String, nullable=True)
    supporting_chunks: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    supporting_documents: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    supporting_sources: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    supporting_collections: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    validation_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("validation_reports.id", ondelete="SET NULL"), nullable=True)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_snapshots.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("ix_evidence_attributions_generation_job_id", "generation_job_id"),
        Index("ix_evidence_attributions_snapshot_id", "snapshot_id"),
    )


class GenerationAnalytics(Base, UUIDMixin):
    __tablename__ = "generation_analytics"

    generation_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("generation_jobs.id", ondelete="CASCADE"), nullable=True)
    collections_used: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    retrieved_documents_count: Mapped[int] = mapped_column(Integer, default=0)
    retrieved_chunks_count: Mapped[int] = mapped_column(Integer, default=0)
    context_size: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    retrieval_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    validation_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_build_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    generation_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    knowledge_reuse_metrics: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    evidence_usage_metrics: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("ix_generation_analytics_generation_job_id", "generation_job_id"),
    )


class GenerationContextCache(Base, UUIDMixin):
    __tablename__ = "generation_context_caches"

    cache_key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    context_package: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("ix_generation_context_caches_cache_key", "cache_key"),
    )
