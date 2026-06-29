from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from pydantic import BaseModel
from typing import Optional, List

from app.api.deps import get_db, get_current_user_placeholder
from app.core.responses import APIResponse, success_response
from app.services.review import review_service
from app.models.enums import ReviewAssignmentStatus, ReviewerRole, CommentActionType, ReviewDecisionType

router = APIRouter()

# --- Pydantic Models ---
class AssignmentRequest(BaseModel):
    reviewer_id: UUID
    role: ReviewerRole = ReviewerRole.primary

class CommentRequest(BaseModel):
    node_stable_id: str
    comment: str

class ReplyRequest(BaseModel):
    comment: str

class DraftRequest(BaseModel):
    decision: Optional[ReviewDecisionType] = None
    summary: Optional[str] = None

class CompleteReviewRequest(BaseModel):
    decision: ReviewDecisionType
    summary: Optional[str] = None

# --- Endpoints ---

@router.get("/queue", response_model=APIResponse[list])
async def get_queue(
    status_filter: Optional[ReviewAssignmentStatus] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """Fetch review queue for the current user."""
    queue = await review_service.get_reviewer_queue(db, UUID(user["id"]), status_filter)
    return success_response(data=[q.id for q in queue], message="Fetched queue")

@router.post("/{document_id}/assignments", response_model=APIResponse[dict])
async def assign_reviewer(
    document_id: UUID,
    req: AssignmentRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    assignment = await review_service.assign_reviewer(db, document_id, req.reviewer_id, req.role)
    return success_response(data={"assignment_id": str(assignment.id)}, message="Reviewer assigned")

@router.post("/{document_id}/comments", response_model=APIResponse[dict])
async def add_comment(
    document_id: UUID,
    req: CommentRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    comment = await review_service.add_comment(
        db, document_id, req.node_stable_id, req.comment, CommentActionType.comment, UUID(user["id"])
    )
    return success_response(data={"comment_id": str(comment.id)}, message="Comment added")

@router.post("/{document_id}/comments/{comment_id}/reply", response_model=APIResponse[dict])
async def reply_to_comment(
    document_id: UUID,
    comment_id: UUID,
    req: ReplyRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    # we don't strictly enforce node_stable_id for a reply, we can just look it up or leave it null 
    # but the service requires node_stable_id. For now, empty string is fine as it's a child comment.
    comment = await review_service.add_comment(
        db, document_id, "", req.comment, CommentActionType.comment, UUID(user["id"]), parent_comment_id=comment_id
    )
    return success_response(data={"comment_id": str(comment.id)}, message="Reply added")

@router.post("/{document_id}/comments/{comment_id}/regenerate", response_model=APIResponse[dict])
async def regenerate_from_comment(
    document_id: UUID,
    comment_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    # First we mark the comment as an ai_request, or we assume it already is?
    # If the user clicks "Regenerate" on an existing comment, we can just call the service.
    # The service expects the comment to have action_type = ai_request.
    # So we should update it first, or let the service do it.
    from app.models.review import ReviewComment
    comment_obj = await db.get(ReviewComment, comment_id)
    if not comment_obj:
        raise HTTPException(status_code=404, detail="Comment not found")
        
    comment_obj.action_type = CommentActionType.ai_request
    await db.commit()
    
    new_version = await review_service.handle_ai_request_from_comment(db, comment_id)
    return success_response(data={"new_version_id": str(new_version.id)}, message="Node regenerated")

@router.post("/{document_id}/draft", response_model=APIResponse[dict])
async def save_draft(
    document_id: UUID,
    req: DraftRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    review = await review_service.save_review_draft(db, document_id, UUID(user["id"]), req.decision, req.summary)
    return success_response(data={"human_review_id": str(review.id)}, message="Draft saved")

@router.post("/{document_id}/complete", response_model=APIResponse[dict])
async def complete_review(
    document_id: UUID,
    req: CompleteReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    review = await review_service.complete_review(db, document_id, UUID(user["id"]), req.decision, req.summary)
    return success_response(data={"human_review_id": str(review.id)}, message="Review completed")
