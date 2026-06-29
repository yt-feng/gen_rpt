from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from pydantic import BaseModel
from typing import List, Optional

from app.api.deps import get_db, get_current_user_placeholder
from app.core.responses import APIResponse, success_response
from app.services.ai_assistant import ai_assistant_service
from app.models.enums import AIProviderType

router = APIRouter()

class ProposalRequest(BaseModel):
    version_id: UUID
    target_node_stable_ids: List[str]
    prompt_text: str
    provider_type: AIProviderType = AIProviderType.groq
    num_alternatives: int = 1

class AcceptProposalRequest(BaseModel):
    draft_version_id: UUID
    modified_content: Optional[str] = None

@router.post("/{document_id}/ai/propose", response_model=APIResponse[dict])
async def generate_proposal(
    document_id: UUID,
    req: ProposalRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    proposals = await ai_assistant_service.generate_proposals(
        db=db,
        document_id=document_id,
        version_id=req.version_id,
        target_node_stable_ids=req.target_node_stable_ids,
        prompt_text=req.prompt_text,
        provider_type=req.provider_type,
        num_alternatives=req.num_alternatives,
        editor_id=UUID(user["id"])
    )
    data = [{"proposal_id": str(p.id), "response": p.response_content} for p in proposals]
    return success_response(data={"proposals": data}, message="Proposals generated")

@router.post("/{document_id}/ai/proposals/{proposal_id}/accept", response_model=APIResponse[dict])
async def accept_proposal(
    document_id: UUID,
    proposal_id: UUID,
    req: AcceptProposalRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    try:
        proposal = await ai_assistant_service.accept_proposal(
            db=db,
            proposal_id=proposal_id,
            reviewer_id=UUID(user["id"]),
            draft_version_id=req.draft_version_id,
            modified_content=req.modified_content
        )
        return success_response(data={"status": proposal.status.value}, message="Proposal accepted and applied to draft")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{document_id}/ai/proposals/{proposal_id}/reject", response_model=APIResponse[dict])
async def reject_proposal(
    document_id: UUID,
    proposal_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    try:
        proposal = await ai_assistant_service.reject_proposal(
            db=db,
            proposal_id=proposal_id,
            reviewer_id=UUID(user["id"])
        )
        return success_response(data={"status": proposal.status.value}, message="Proposal rejected")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
