import uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, ForeignKey
from sqlalchemy.dialects.postgresql import ENUM
from app.models.base import Base, UUIDMixin
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
    github_run: Mapped[str | None] = mapped_column(String, nullable=True)
    workflow: Mapped[str | None] = mapped_column(String, nullable=True)
    
    status: Mapped[JobStatusType] = mapped_column(
        ENUM(JobStatusType, name="job_status_type", create_type=False),
        default=JobStatusType.pending
    )
    
    started: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    completed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    logs: Mapped[str | None] = mapped_column(String, nullable=True)

class PublishJob(Base, UUIDMixin):
    __tablename__ = "publish_jobs"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    
    status: Mapped[JobStatusType] = mapped_column(
        ENUM(JobStatusType, name="job_status_type", create_type=False),
        default=JobStatusType.pending
    )
    
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
