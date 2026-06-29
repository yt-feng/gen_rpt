import uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, ForeignKey, Boolean, Enum
from app.models.base import Base, UUIDMixin, JSONVariant
from app.models.enums import JobStatusType, BlockActor
from datetime import datetime
from sqlalchemy import DateTime, func

class AIEditRequest(Base, UUIDMixin):
    __tablename__ = "ai_edit_requests"
    block_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_blocks.id", ondelete="CASCADE"), nullable=False)
    prompt: Mapped[str] = mapped_column(String, nullable=False)
    instruction: Mapped[str] = mapped_column(String, nullable=False)
    
    status: Mapped[JobStatusType] = mapped_column(
        Enum(JobStatusType, name="job_status_type", create_type=False),
        default=JobStatusType.pending
    )
    
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

class AIEditResult(Base, UUIDMixin):
    __tablename__ = "ai_edit_results"
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_edit_requests.id", ondelete="CASCADE"), nullable=False)
    old_content: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    new_content: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class BlockEdit(Base, UUIDMixin):
    __tablename__ = "block_edits"
    block_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_blocks.id", ondelete="CASCADE"), nullable=False)
    old_value: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    new_value: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    edited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

class ChangeHistory(Base, UUIDMixin):
    __tablename__ = "change_history"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    entity: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    change_type: Mapped[str] = mapped_column(String, nullable=False)
    
    actor: Mapped[BlockActor] = mapped_column(
        Enum(BlockActor, name="block_actor", create_type=False),
        nullable=False
    )
    
    old_value: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
