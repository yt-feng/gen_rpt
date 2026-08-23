# review_service.py
# authoritative service database persistence layer for human claims, reviews, and comments

import uuid
from typing import List, Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.models.review import HumanReview, ReviewComment, AIReview, ReviewClaim
from app.models.enums import ReviewDecisionType, CommentActionType

class ReviewService:
    @staticmethod
    async def create_or_update_human_review(
        db: AsyncSession,
        version_id: uuid.UUID,
        reviewer_id: uuid.UUID,
        decision: ReviewDecisionType,
        summary: Optional[str] = None
    ) -> HumanReview:
        stmt = select(HumanReview).where(HumanReview.version_id == version_id)
        res = await db.execute(stmt)
        review = res.scalar_one_or_none()
        
        if not review:
            review = HumanReview(
                version_id=version_id,
                reviewer=reviewer_id,
                decision=decision,
                summary=summary,
                is_draft=False,
                completed_at=datetime.now(timezone.utc)
            )
            db.add(review)
        else:
            review.decision = decision
            review.summary = summary
            review.reviewer = reviewer_id
            review.is_draft = False
            review.completed_at = datetime.now(timezone.utc)
        
        await db.commit()
        await db.refresh(review)
        return review

    @staticmethod
    async def add_review_comment(
        db: AsyncSession,
        document_id: uuid.UUID,
        comment_text: str,
        created_by: uuid.UUID,
        human_review_id: Optional[uuid.UUID] = None,
        section_id: Optional[uuid.UUID] = None,
        block_id: Optional[uuid.UUID] = None,
        node_stable_id: Optional[str] = None,
        priority: str = "normal"
    ) -> ReviewComment:
        comment = ReviewComment(
            human_review_id=human_review_id,
            document_id=document_id,
            section_id=section_id,
            block_id=block_id,
            node_stable_id=node_stable_id,
            comment=comment_text,
            priority=priority,
            created_by=created_by,
            resolved=False,
            action_type=CommentActionType.comment
        )
        db.add(comment)
        await db.commit()
        await db.refresh(comment)
        return comment

    @staticmethod
    async def list_document_comments(db: AsyncSession, document_id: uuid.UUID) -> List[ReviewComment]:
        stmt = select(ReviewComment).where(ReviewComment.document_id == document_id).order_by(ReviewComment.created_at.asc())
        res = await db.execute(stmt)
        return list(res.scalars().all())
