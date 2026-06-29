import uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, ForeignKey, DateTime, Enum
from app.models.base import Base, UUIDMixin, JSONVariant
from app.models.enums import EditorActionType

class NodeLock(Base, UUIDMixin):
    __tablename__ = "node_locks"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    node_stable_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    locked_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)

class NodeEditHistory(Base, UUIDMixin):
    __tablename__ = "node_edit_histories"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    node_stable_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    editor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    edit_type: Mapped[EditorActionType] = mapped_column(
        Enum(EditorActionType, name="editor_action_type", create_type=False),
        nullable=False
    )
    
    old_value: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
