import uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, ForeignKey, Float
from app.models.base import Base, UUIDMixin, TimestampMixin, JSONVariant

class IterationHistory(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "iteration_history"
    
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    stable_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    
    actor_type: Mapped[str] = mapped_column(String, nullable=False)  # "AI" or "Human"
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    previous_content: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    new_content: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    
    # AI specific fields
    prompt: Mapped[str | None] = mapped_column(String, nullable=True)
    context_used: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    execution_time: Mapped[float | None] = mapped_column(Float, nullable=True)
