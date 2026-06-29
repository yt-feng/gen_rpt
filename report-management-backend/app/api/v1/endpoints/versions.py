from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.api.deps import get_db, get_current_user
from app.core.responses import APIResponse, success_response
from app.models.document import DocumentVersion, Document
from app.services.versioning import versioning_service
from app.models.identity import User

router = APIRouter()

@router.get("/{document_id}/versions", response_model=APIResponse[list])
async def list_versions(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    List all versions for a document.
    """
    stmt = select(DocumentVersion).where(
        DocumentVersion.document_id == document_id
    ).order_by(DocumentVersion.version_number.desc())
    
    result = await db.execute(stmt)
    versions = result.scalars().all()
    
    data = [{
        "id": str(v.id),
        "version_number": v.version_number,
        "change_type": v.change_type.value,
        "created_by": str(v.created_by) if v.created_by else None,
        "actor_type": v.actor_type,
        "created_at": v.created_at,
        "summary": v.summary,
        "release_status": v.release_status.value if v.release_status else None
    } for v in versions]
    
    return success_response(data=data, message="Fetched document versions")

@router.get("/{document_id}/versions/{version_a_id}/compare/{version_b_id}", response_model=APIResponse[dict])
async def compare_versions(
    document_id: UUID,
    version_a_id: UUID,
    version_b_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Compare two versions structurally based on stable_id.
    """
    diff = await versioning_service.compare_versions(db, version_a_id, version_b_id)
    return success_response(data=diff, message="Version comparison completed")

@router.post("/{document_id}/versions/{target_version_id}/restore", response_model=APIResponse[dict])
async def restore_version(
    document_id: UUID,
    target_version_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Restore a document to a previous version by deep copying it into a new version.
    """
    stmt = select(Document.current_version_id).where(Document.id == document_id)
    current_v_id = (await db.execute(stmt)).scalars().first()
    if not current_v_id:
        raise HTTPException(status_code=404, detail="Document has no current version")
        
    async with db.begin():
        new_version = await versioning_service.restore_version(
            db=db,
            document_id=document_id,
            current_version_id=current_v_id,
            target_version_id=target_version_id,
            actor_id=user.id
        )
        
    return success_response(
        data={"new_version_id": str(new_version.id), "version_number": new_version.version_number},
        message="Version restored successfully"
    )

@router.post("/{document_id}/nodes/{stable_id}/rollback/{target_version_id}", response_model=APIResponse[dict])
async def rollback_node(
    document_id: UUID,
    stable_id: str,
    target_version_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Rollback a specific node to a past state, creating a new DocumentVersion.
    """
    stmt = select(Document.current_version_id).where(Document.id == document_id)
    current_v_id = (await db.execute(stmt)).scalars().first()
    if not current_v_id:
        raise HTTPException(status_code=404, detail="Document has no current version")
        
    async with db.begin():
        new_version = await versioning_service.rollback_node(
            db=db,
            document_id=document_id,
            current_version_id=current_v_id,
            target_version_id=target_version_id,
            node_stable_id=stable_id,
            actor_id=user.id
        )
        
    return success_response(
        data={"new_version_id": str(new_version.id), "version_number": new_version.version_number},
        message="Node rolled back successfully"
    )
