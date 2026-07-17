import uuid
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from app.models.review import ReviewAssignment, HumanReview, ReviewComment
from app.models.document import Document, DocumentBlock, DocumentSection, DocumentVersion
from app.models.enums import ReviewAssignmentStatus, ReviewerRole, CommentActionType, ReviewDecisionType, DocStatus, DocChangeType

from app.services.canonical import VersionManager
from app.services.snapshot import snapshot_engine

class ReviewService:
    @staticmethod
    async def assign_reviewer(db: AsyncSession, document_id: uuid.UUID, reviewer_id: uuid.UUID, role: ReviewerRole) -> ReviewAssignment:
        assignment = ReviewAssignment(
            id=uuid.uuid4(),
            document_id=document_id,
            reviewer_id=reviewer_id,
            role=role,
            status=ReviewAssignmentStatus.pending
        )
        db.add(assignment)
        
        doc = await db.get(Document, document_id)
        if doc and doc.status in (DocStatus.draft, DocStatus.ai_reviewed):
            doc.status = DocStatus.assigned
            
        await db.commit()
        await db.refresh(assignment)
        return assignment

    @staticmethod
    async def get_reviewer_queue(db: AsyncSession, reviewer_id: uuid.UUID, status_filter: Optional[ReviewAssignmentStatus] = None) -> List[ReviewAssignment]:
        stmt = select(ReviewAssignment).where(ReviewAssignment.reviewer_id == reviewer_id)
        if status_filter:
            stmt = stmt.where(ReviewAssignment.status == status_filter)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def add_comment(
        db: AsyncSession, 
        document_id: uuid.UUID, 
        node_stable_id: str, 
        comment: str, 
        action_type: CommentActionType, 
        user_id: uuid.UUID,
        parent_comment_id: Optional[uuid.UUID] = None
    ) -> ReviewComment:
        rev_comment = ReviewComment(
            id=uuid.uuid4(),
            document_id=document_id,
            node_stable_id=node_stable_id,
            comment=comment,
            action_type=action_type,
            created_by=user_id,
            parent_comment_id=parent_comment_id
        )
        db.add(rev_comment)
        await db.commit()
        await db.refresh(rev_comment)
        return rev_comment

    @staticmethod
    async def save_review_draft(db: AsyncSession, document_id: uuid.UUID, reviewer_id: uuid.UUID, decision: Optional[ReviewDecisionType], summary: Optional[str]) -> HumanReview:
        doc = await db.get(Document, document_id)
        stmt = select(HumanReview).where(HumanReview.version_id == doc.current_version_id, HumanReview.reviewer == reviewer_id)
        result = await db.execute(stmt)
        review = result.scalars().first()
        
        if not review:
            review = HumanReview(
                id=uuid.uuid4(),
                version_id=doc.current_version_id,
                reviewer=reviewer_id,
                decision=decision,
                summary=summary,
                is_draft=True
            )
            db.add(review)
        else:
            review.decision = decision
            review.summary = summary
            review.is_draft = True
            
        # Update assignment to in_progress
        assign_stmt = select(ReviewAssignment).where(ReviewAssignment.document_id == document_id, ReviewAssignment.reviewer_id == reviewer_id)
        assign_res = await db.execute(assign_stmt)
        assignment = assign_res.scalars().first()
        if assignment and assignment.status == ReviewAssignmentStatus.pending:
            assignment.status = ReviewAssignmentStatus.in_progress
            doc.status = DocStatus.in_review
            
        await db.commit()
        await db.refresh(review)
        return review

    @staticmethod
    async def complete_review(db: AsyncSession, document_id: uuid.UUID, reviewer_id: uuid.UUID, decision: ReviewDecisionType, summary: Optional[str]) -> HumanReview:
        doc = await db.get(Document, document_id)
        stmt = select(HumanReview).where(HumanReview.version_id == doc.current_version_id, HumanReview.reviewer == reviewer_id)
        result = await db.execute(stmt)
        review = result.scalars().first()
        
        if not review:
            review = HumanReview(
                id=uuid.uuid4(),
                version_id=doc.current_version_id,
                reviewer=reviewer_id,
                decision=decision,
                summary=summary,
                is_draft=False,
                completed_at=datetime.utcnow()
            )
            db.add(review)
        else:
            review.decision = decision
            review.summary = summary
            review.is_draft = False
            review.completed_at = datetime.utcnow()
            
        # State machine
        if decision == ReviewDecisionType.approved:
            doc.status = DocStatus.ready_for_publish
        elif decision == ReviewDecisionType.needs_revision:
            doc.status = DocStatus.needs_revision
        elif decision == ReviewDecisionType.rejected:
            doc.status = DocStatus.rejected
            
        assign_stmt = select(ReviewAssignment).where(ReviewAssignment.document_id == document_id, ReviewAssignment.reviewer_id == reviewer_id)
        assign_res = await db.execute(assign_stmt)
        assignment = assign_res.scalars().first()
        if assignment:
            assignment.status = ReviewAssignmentStatus.completed
            
        await db.commit()
        await db.refresh(review)

        # Trigger review snapshot creation
        try:
            from app.services.review_integration import review_snapshot_service
            await review_snapshot_service.create_review_snapshot(
                db=db,
                version_id=doc.current_version_id,
                reviewer_id=reviewer_id,
                human_review_id=review.id
            )
        except Exception as e:
            print(f"[complete_review] Failed to create review snapshot: {e}")

        return review


    @staticmethod
    async def handle_ai_request_from_comment(db: AsyncSession, comment_id: uuid.UUID) -> DocumentVersion:
        comment = await db.get(ReviewComment, comment_id)
        if not comment or comment.action_type != CommentActionType.ai_request:
            raise ValueError("Invalid comment for AI request")
            
        doc = await db.get(Document, comment.document_id)
        if not doc.current_version_id:
            raise ValueError("No active version found")
            
        doc.status = DocStatus.waiting_for_ai
        await db.commit()
        
        # 1. Create a new Version (AI_REGENERATION)
        new_version = await VersionManager.create_new_version(
            db=db,
            document_id=doc.id,
            parent_version_id=doc.current_version_id,
            change_type=DocChangeType.AI_REGENERATION,
            actor_id=comment.created_by,
            summary=f"AI regeneration requested from comment {comment.id}"
        )
        
        # 2. Modify the targeted node
        stmt = select(DocumentBlock).join(DocumentSection).where(
            DocumentSection.version_id == new_version.id,
            DocumentBlock.stable_id == comment.node_stable_id
        )
        result = await db.execute(stmt)
        block = result.scalars().first()
        
        if block:
            # Mocking AI engine response based on comment prompt
            block.markdown = f"{block.markdown}\n\nRevised content based on feedback: {comment.comment}"
            
        doc.current_version_id = new_version.id
        doc.status = DocStatus.in_review
        await db.commit()
        
        # 3. Snapshot Engine
        await snapshot_engine.generate_snapshot(db, doc.id, new_version.id)
        
        return new_version

review_service = ReviewService()
