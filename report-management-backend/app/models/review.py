import uuid
from typing import Optional
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, ForeignKey, Numeric, Boolean, Enum
from app.models.base import Base, UUIDMixin
from app.models.enums import ReviewDecisionType, ReviewAssignmentStatus, ReviewerRole, CommentActionType
from datetime import datetime
from sqlalchemy import DateTime, func

class ReviewAssignment(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "review_assignments"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    role: Mapped[ReviewerRole] = mapped_column(
        Enum(ReviewerRole, name="reviewer_role", create_type=False),
        nullable=False, default=ReviewerRole.primary
    )
    status: Mapped[ReviewAssignmentStatus] = mapped_column(
        Enum(ReviewAssignmentStatus, name="review_assignment_status", create_type=False),
        nullable=False, default=ReviewAssignmentStatus.pending
    )
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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

class HumanReview(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "human_reviews"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    reviewer: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    decision: Mapped[ReviewDecisionType | None] = mapped_column(
        Enum(ReviewDecisionType, name="review_decision_type", create_type=False),
        nullable=True
    )
    
    is_draft: Mapped[bool] = mapped_column(Boolean, default=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class ReviewComment(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "review_comments"
    # Nullable because we can tie it directly to document/version instead if we want, or keep it under human_review
    human_review_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("human_reviews.id", ondelete="CASCADE"), nullable=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    
    parent_comment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("review_comments.id", ondelete="CASCADE"), nullable=True)
    
    section_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_sections.id", ondelete="CASCADE"), nullable=True)
    block_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_blocks.id", ondelete="CASCADE"), nullable=True)
    node_stable_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    
    action_type: Mapped[CommentActionType] = mapped_column(
        Enum(CommentActionType, name="comment_action_type", create_type=False),
        default=CommentActionType.comment
    )
    
    comment: Mapped[str] = mapped_column(String, nullable=False)
    priority: Mapped[str] = mapped_column(String, default="normal")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
