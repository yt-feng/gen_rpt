import uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, ForeignKey, Enum, Integer
from app.models.base import Base, UUIDMixin, JSONVariant
from app.models.enums import JobStatusType
from datetime import datetime
from sqlalchemy import DateTime, func

class WorkflowInstance(Base, UUIDMixin):
    __tablename__ = "workflow_instances"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    current_state: Mapped[str] = mapped_column(String, nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class WorkflowEvent(Base, UUIDMixin):
    __tablename__ = "workflow_events"
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_instances.id", ondelete="CASCADE"), nullable=False)
    previous_state: Mapped[str | None] = mapped_column(String, nullable=True)
    current_state: Mapped[str] = mapped_column(String, nullable=False)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

class GenerationJob(Base, UUIDMixin):
    __tablename__ = "generation_jobs"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    
    topic: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt: Mapped[str | None] = mapped_column(String, nullable=True)
    report_type: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    token_usage: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    
    artifacts: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    version: Mapped[str | None] = mapped_column(String, nullable=True)
    
    errors: Mapped[str | None] = mapped_column(String, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    audit_metadata: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    github_run: Mapped[str | None] = mapped_column(String, nullable=True)
    workflow: Mapped[str | None] = mapped_column(String, nullable=True)
    
    status: Mapped[JobStatusType] = mapped_column(
        Enum(JobStatusType, name="job_status_type", create_type=False),
        default=JobStatusType.pending
    )
    
    started: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    completed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logs: Mapped[str | None] = mapped_column(String, nullable=True)

class PublishJob(Base, UUIDMixin):
    __tablename__ = "publish_jobs"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    
    status: Mapped[JobStatusType] = mapped_column(
        Enum(JobStatusType, name="job_status_type", create_type=False),
        default=JobStatusType.pending
    )
    
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class GateXPublication(Base, UUIDMixin):
    """
    Stores the external identifiers and state returned by the GateX (MENA Compass)
    Bulk Report Ingestion API after a successful publish operation.
    This is the authoritative record of what was sent to GateX and when.
    Do not overwrite — append new records for re-publishes.
    """
    __tablename__ = "gatex_publications"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    # The document version that was published
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )

    # External identifiers returned by GateX
    external_report_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_object_key: Mapped[str | None] = mapped_column(String, nullable=True)  # data.key from REPORT_ORIGINAL presign
    cover_image_key: Mapped[str | None] = mapped_column(String, nullable=True)       # data.key from REPORT_IMAGE presign

    # Publication state
    publish_status: Mapped[str] = mapped_column(String, nullable=False, default="publishing")
    external_response: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)

    # Audit fields
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    publish_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

