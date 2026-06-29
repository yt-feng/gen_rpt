import uuid
from typing import Optional
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, ForeignKey, Numeric, Boolean, Enum
from app.models.base import Base, UUIDMixin
from app.models.enums import ReviewDecisionType
from datetime import datetime
from sqlalchemy import DateTime, func

class AIReview(Base, UUIDMixin):
    __tablename__ = "ai_reviews"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    overall_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    grade: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="completed")
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

class ReviewScore(Base, UUIDMixin):
    __tablename__ = "review_scores"
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_reviews.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    comment: Mapped[str | None] = mapped_column(String, nullable=True)

class ReviewFinding(Base, UUIDMixin):
    __tablename__ = "review_findings"
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_reviews.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    recommendation: Mapped[str | None] = mapped_column(String, nullable=True)

class ReviewClaim(Base, UUIDMixin):
    __tablename__ = "review_claims"
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_reviews.id", ondelete="CASCADE"), nullable=False)
    claim: Mapped[str] = mapped_column(String, nullable=False)
    risk: Mapped[str] = mapped_column(String, nullable=False)
    citation: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str] = mapped_column(String, nullable=False)

class HumanReview(Base, UUIDMixin):
    __tablename__ = "human_reviews"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    reviewer: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    decision: Mapped[ReviewDecisionType] = mapped_column(
        Enum(ReviewDecisionType, name="review_decision_type", create_type=False),
        nullable=False
    )
    
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

class ReviewComment(Base, UUIDMixin):
    __tablename__ = "review_comments"
    human_review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("human_reviews.id", ondelete="CASCADE"), nullable=False)
    section_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_sections.id", ondelete="CASCADE"), nullable=True)
    block_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_blocks.id", ondelete="CASCADE"), nullable=True)
    node_stable_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    comment: Mapped[str] = mapped_column(String, nullable=False)
    priority: Mapped[str] = mapped_column(String, default="normal")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
