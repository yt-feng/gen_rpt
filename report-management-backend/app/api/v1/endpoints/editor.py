from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from pydantic import BaseModel
from typing import Optional, Dict, Any

from app.api.deps import get_db, get_current_user_placeholder
from app.core.responses import APIResponse, success_response
from app.services.editor import editor_service

router = APIRouter()

class LockRequest(BaseModel):
    timeout_minutes: int = 5

class AutosaveRequest(BaseModel):
    payload: Dict[str, Any]
    reason: Optional[str] = "Manual Edit"

class AINodeRequest(BaseModel):
    prompt: str

@router.post("/{document_id}/draft/start", response_model=APIResponse[dict])
async def start_draft(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    draft_version = await editor_service.start_draft_session(db, document_id, UUID(user["id"]))
    return success_response(data={"draft_version_id": str(draft_version.id)}, message="Draft started")

@router.post("/{document_id}/draft/{draft_id}/commit", response_model=APIResponse[dict])
async def commit_draft(
    document_id: UUID,
    draft_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    version = await editor_service.commit_draft_session(db, document_id, draft_id, UUID(user["id"]))
    return success_response(data={"version_id": str(version.id)}, message="Draft committed and HTML synchronized")

@router.post("/{document_id}/nodes/{node_id}/lock", response_model=APIResponse[dict])
async def lock_node(
    document_id: UUID,
    node_id: str,
    req: LockRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    try:
        lock = await editor_service.acquire_lock(db, document_id, node_id, UUID(user["id"]), req.timeout_minutes)
        return success_response(data={"lock_id": str(lock.id), "expires_at": lock.expires_at.isoformat()}, message="Node locked")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

@router.delete("/{document_id}/nodes/{node_id}/lock", response_model=APIResponse[bool])
async def unlock_node(
    document_id: UUID,
    node_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    released = await editor_service.release_lock(db, document_id, node_id, UUID(user["id"]))
    return success_response(data=released, message="Lock released")

@router.put("/{document_id}/draft/{draft_id}/nodes/{node_id}/autosave", response_model=APIResponse[dict])
async def autosave_node(
    document_id: UUID,
    draft_id: UUID,
    node_id: str,
    req: AutosaveRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    # Strictly we should check if they hold the lock. 
    # For now, we assume frontend ensures locking before saving.
    history = await editor_service.update_node_content(db, draft_id, node_id, req.payload, UUID(user["id"]), req.reason)
    return success_response(data={"history_id": str(history.id)}, message="Node autosaved")

@router.post("/{document_id}/draft/{draft_id}/nodes/{node_id}/ai", response_model=APIResponse[dict])
async def ai_node_rewrite(
    document_id: UUID,
    draft_id: UUID,
    node_id: str,
    req: AINodeRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    history = await editor_service.ai_node_rewrite(db, draft_id, node_id, req.prompt, UUID(user["id"]))
    return success_response(data={"history_id": str(history.id)}, message="AI edit applied")
