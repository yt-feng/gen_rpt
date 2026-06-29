import uuid
from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db, get_current_user
from app.core.responses import APIResponse, success_response
from app.models.document import Document, DocumentVersion
from app.services.iteration import iteration_engine
from app.services.rendering import rendering_pipeline
from app.models.identity import User

router = APIRouter()

class RegenerateRequest(BaseModel):
    parent_version_id: uuid.UUID
    instruction: str = Field(..., min_length=1)

class HumanEditRequest(BaseModel):
    parent_version_id: uuid.UUID
    new_markdown: str = Field(...)

@router.get("/{document_id}/canonical", response_model=APIResponse[List[Dict[str, Any]]])
async def get_canonical_document(
    document_id: uuid.UUID,
    version_id: uuid.UUID = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Fetches the structured canonical JSON representation of the document.
    """
    if not version_id:
        # Get latest version
        stmt = select(Document.current_version_id).where(Document.id == document_id)
        result = await db.execute(stmt)
        version_id = result.scalars().first()
        if not version_id:
            raise HTTPException(status_code=404, detail="Document version not found")

    sections = await rendering_pipeline.get_version_tree(db, version_id)
    
    # Serialize to JSON-friendly format
    data = []
    for sec in sections:
        sec_data = {
            "stable_id": sec.stable_id,
            "title": sec.title,
            "order": sec.section_order,
            "blocks": []
        }
        for block in sec.blocks:
            sec_data["blocks"].append({
                "stable_id": block.stable_id,
                "type": block.block_type.value,
                "order": block.block_order,
                "markdown": block.markdown,
                "content": block.content_json
            })
        data.append(sec_data)
        
    return success_response(data=data, message="Canonical document retrieved")

@router.post("/{document_id}/nodes/{stable_id}/regenerate", response_model=APIResponse[dict])
async def regenerate_block(
    document_id: uuid.UUID,
    stable_id: str,
    payload: RegenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Context-aware AI block-level regeneration. Creates a new DocumentVersion.
    """
    new_version = await iteration_engine.regenerate_node(
        db=db,
        document_id=document_id,
        parent_version_id=payload.parent_version_id,
        stable_id=stable_id,
        instruction=payload.instruction,
        actor_id=current_user.id
    )
    
    return success_response(
        data={"new_version_id": str(new_version.id), "version_number": new_version.version_number},
        message="Block regenerated successfully"
    )

@router.put("/{document_id}/nodes/{stable_id}", response_model=APIResponse[dict])
async def human_edit_block(
    document_id: uuid.UUID,
    stable_id: str,
    payload: HumanEditRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Human edit on a block. Creates a new DocumentVersion.
    """
    new_version = await iteration_engine.human_edit_node(
        db=db,
        document_id=document_id,
        parent_version_id=payload.parent_version_id,
        stable_id=stable_id,
        new_markdown=payload.new_markdown,
        actor_id=current_user.id
    )
    
    return success_response(
        data={"new_version_id": str(new_version.id), "version_number": new_version.version_number},
        message="Block updated successfully"
    )
