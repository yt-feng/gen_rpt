import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.ai import AIProposal, AIPromptTemplate
from app.models.document import DocumentBlock, DocumentSection, DocumentVersion, Document
from app.models.enums import AIProviderType, ProposalStatus, ReleaseStatus
from app.services.ai_providers import AIProviderFactory
from app.services.editor import editor_service

class AIAssistantService:
    @staticmethod
    async def generate_proposals(
        db: AsyncSession,
        document_id: uuid.UUID,
        version_id: uuid.UUID,
        target_node_stable_ids: List[str],
        prompt_text: str,
        provider_type: AIProviderType = AIProviderType.groq,
        num_alternatives: int = 1,
        editor_id: Optional[uuid.UUID] = None
    ) -> List[AIProposal]:
        """
        Generates 1 to N proposals for the selected nodes.
        """
        # Fetch Context (mocking complex retrieval for now)
        context_nodes = []
        for n_id in target_node_stable_ids:
            stmt = select(DocumentBlock).join(DocumentSection).where(
                DocumentSection.version_id == version_id,
                DocumentBlock.stable_id == n_id
            )
            res = await db.execute(stmt)
            block = res.scalars().first()
            if block:
                context_nodes.append({"stable_id": n_id, "markdown": block.markdown})
        
        context_bundle = {"nodes": context_nodes, "document_id": str(document_id)}
        
        provider = AIProviderFactory.get_provider(provider_type)
        
        proposals = []
        for i in range(num_alternatives):
            # Pass prompt text (could append variation instruction)
            p_text = f"{prompt_text} (Alternative {i+1})" if num_alternatives > 1 else prompt_text
            
            result = await provider.generate_proposal(context_bundle, p_text)
            
            proposal = AIProposal(
                id=uuid.uuid4(),
                document_id=document_id,
                target_node_stable_ids=target_node_stable_ids,
                context_bundle=context_bundle,
                model_provider=provider_type,
                model_version=result["model_version"],
                response_content=result["response_content"],
                status=ProposalStatus.pending,
                reviewer_id=editor_id,
                timestamp=datetime.now(timezone.utc),
                execution_time_ms=result["execution_time_ms"],
                prompt_tokens=result["prompt_tokens"],
                completion_tokens=result["completion_tokens"]
            )
            db.add(proposal)
            proposals.append(proposal)
            
        await db.commit()
        for p in proposals:
            await db.refresh(p)
            
        return proposals

    @staticmethod
    async def accept_proposal(
        db: AsyncSession,
        proposal_id: uuid.UUID,
        reviewer_id: uuid.UUID,
        draft_version_id: uuid.UUID,
        modified_content: Optional[str] = None
    ) -> AIProposal:
        """
        Accepts a proposal and applies it to a draft version via EditorService.
        """
        proposal = await db.get(AIProposal, proposal_id)
        if not proposal:
            raise ValueError("Proposal not found")
            
        if proposal.status != ProposalStatus.pending:
            raise ValueError("Only pending proposals can be accepted")
            
        final_content = modified_content if modified_content else proposal.response_content.get("proposed_content", "")
        
        # Apply changes to nodes
        for node_id in proposal.target_node_stable_ids:
            payload = {"markdown": final_content}
            await editor_service.update_node_content(db, draft_version_id, node_id, payload, reviewer_id, reason="AI Proposal Acceptance")
            
        proposal.status = ProposalStatus.modified_accepted if modified_content else ProposalStatus.accepted
        await db.commit()
        await db.refresh(proposal)
        return proposal

    @staticmethod
    async def reject_proposal(
        db: AsyncSession,
        proposal_id: uuid.UUID,
        reviewer_id: uuid.UUID
    ) -> AIProposal:
        proposal = await db.get(AIProposal, proposal_id)
        if not proposal:
            raise ValueError("Proposal not found")
            
        proposal.status = ProposalStatus.rejected
        await db.commit()
        await db.refresh(proposal)
        return proposal

ai_assistant_service = AIAssistantService()
