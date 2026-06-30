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
