from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from app.models.enums import ReviewDecisionType

class AIReviewBase(BaseModel):
    model: str
    provider: str
    overall_score: Optional[float] = None
    grade: Optional[str] = None
    summary: Optional[str] = None
    status: str = "completed"

class AIReviewResponse(AIReviewBase):
    id: UUID
    version_id: UUID
    reviewed_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class HumanReviewBase(BaseModel):
    decision: ReviewDecisionType
    summary: Optional[str] = None

class HumanReviewResponse(HumanReviewBase):
    id: UUID
    version_id: UUID
    reviewer: Optional[UUID] = None
    completed_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class ReviewCommentBase(BaseModel):
    comment: str
    priority: str = "normal"
    resolved: bool = False

class ReviewCommentResponse(ReviewCommentBase):
    id: UUID
    human_review_id: UUID
    section_id: Optional[UUID] = None
    block_id: Optional[UUID] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ReviewCommentCreate(BaseModel):
    comment: str
    priority: Optional[str] = "normal"
    section_id: Optional[UUID] = None
    block_id: Optional[UUID] = None
    node_stable_id: Optional[str] = None
