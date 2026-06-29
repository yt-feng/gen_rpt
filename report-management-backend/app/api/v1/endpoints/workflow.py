from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.api.deps import get_db, get_current_user_placeholder
from app.core.responses import APIResponse, success_response

router = APIRouter()

@router.get("/{document_id}/workflow/state", response_model=APIResponse[dict])
async def get_workflow_state(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Get current workflow state and stage.
    """
    return success_response(data={"state": "DRAFT"}, message="Fetched workflow state")

@router.post("/{document_id}/workflow/transitions", response_model=APIResponse[dict])
async def transition_workflow(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Transition workflow to the next state.
    """
    return success_response(data={}, message="Workflow transitioned successfully")
