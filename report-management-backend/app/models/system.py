import uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, ForeignKey, Boolean
from app.models.base import Base, UUIDMixin, JSONVariant
from datetime import datetime
from sqlalchemy import DateTime, func

class Notification(Base, UUIDMixin):
    __tablename__ = "notifications"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

class ActivityLog(Base, UUIDMixin):
    __tablename__ = "activity_logs"
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONVariant, default=lambda: {})
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

class AuditLog(Base, UUIDMixin):
    __tablename__ = "audit_logs"
    table_name: Mapped[str] = mapped_column(String, nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    old_data: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    new_data: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
