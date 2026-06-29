from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.api.deps import get_db, get_current_user_placeholder
from app.core.responses import APIResponse, success_response

router = APIRouter()

@router.get("/{document_id}/versions", response_model=APIResponse[list])
async def list_versions(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    List all versions for a document.
    """
    return success_response(data=[], message="Fetched document versions")

@router.post("/{document_id}/versions/{version_id}/restore", response_model=APIResponse[dict])
async def restore_version(
    document_id: UUID,
    version_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Restore a document to a previous version.
    """
    return success_response(data={"status": "restored"}, message="Version restored successfully")
