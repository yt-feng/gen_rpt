from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel, Field
from uuid import UUID
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.responses import APIResponse, success_response
from app.api.deps import get_db
from app.services.workflow import workflow_service
from app.logging.logger import logger

router = APIRouter()

def verify_internal_token(x_internal_token: str = Header(...)):
    """Verifies internal requests."""
    # Placeholder for actual internal token check
    if x_internal_token != "trusted-worker-secret":
        raise HTTPException(status_code=403, detail="Invalid internal token")

class WorkflowEventPayload(BaseModel):
    document_id: UUID = Field(description="Target document ID")
    idempotency_key: str = Field(description="Unique key to prevent duplicate processing")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    actor_id: Optional[UUID] = None

@router.post("/events/report-generated", response_model=APIResponse[dict], dependencies=[Depends(verify_internal_token)])
async def handle_report_generated(
    payload: WorkflowEventPayload,
    db: AsyncSession = Depends(get_db)
):
    """Webhook invoked by GitHub Actions when report generation completes."""
    result = await workflow_service.process_workflow_event(
        db=db,
        document_id=payload.document_id,
        event_type="report_generated",
        idempotency_key=payload.idempotency_key,
        new_state="GENERATED",
        actor_id=payload.actor_id,
        metadata=payload.metadata
    )
    return success_response(data=result, message="Event processed")

@router.post("/events/review-generated", response_model=APIResponse[dict], dependencies=[Depends(verify_internal_token)])
async def handle_review_generated(
    payload: WorkflowEventPayload,
    db: AsyncSession = Depends(get_db)
):
    """Webhook invoked by GitHub Actions when AI review generation completes."""
    result = await workflow_service.process_workflow_event(
        db=db,
        document_id=payload.document_id,
        event_type="review_generated",
        idempotency_key=payload.idempotency_key,
        new_state="AI_REVIEWED",
        actor_id=payload.actor_id,
        metadata=payload.metadata
    )
    return success_response(data=result, message="Event processed")

@router.post("/events/upload-complete", response_model=APIResponse[dict], dependencies=[Depends(verify_internal_token)])
async def handle_upload_complete(
    payload: WorkflowEventPayload,
    db: AsyncSession = Depends(get_db)
):
    """Webhook invoked when artifacts are fully uploaded to R2."""
    result = await workflow_service.process_workflow_event(
        db=db,
        document_id=payload.document_id,
        event_type="upload_complete",
        idempotency_key=payload.idempotency_key,
        new_state="PENDING_HUMAN_REVIEW", # Arbitrary logic for now
        actor_id=payload.actor_id,
        metadata=payload.metadata
    )
    return success_response(data=result, message="Event processed")

@router.post("/events/publish-requested", response_model=APIResponse[dict], dependencies=[Depends(verify_internal_token)])
async def handle_publish_requested(
    payload: WorkflowEventPayload,
    db: AsyncSession = Depends(get_db)
):
    result = await workflow_service.process_workflow_event(
        db=db,
        document_id=payload.document_id,
        event_type="publish_requested",
        idempotency_key=payload.idempotency_key,
        new_state="PUBLISHED",
        actor_id=payload.actor_id,
        metadata=payload.metadata
    )
    return success_response(data=result, message="Event processed")
