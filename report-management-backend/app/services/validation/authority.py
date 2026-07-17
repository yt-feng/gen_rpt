import uuid
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.knowledge import KnowledgeDocument
from app.models.validation import ValidationPolicy

class AuthorityService:
    async def calculate_authority(
        self,
        db: AsyncSession,
        documents: List[KnowledgeDocument],
        policy: ValidationPolicy
    ) -> Dict[uuid.UUID, float]:
        """
        Calculates authority score for each document/source based on policy rules.
        """
        rules = policy.rules or {}
        authority_scores = {}
        
        # Load score mappings from rules, with fallback defaults
        type_scores = {
            "government": rules.get("government_authority_score", 1.0),
            "research": rules.get("research_authority_score", 0.8),
            "internal": rules.get("internal_authority_score", 0.9),
            "industry_standards": rules.get("industry_standards_authority_score", 0.8),
            "enterprise_knowledge": rules.get("enterprise_knowledge_authority_score", 0.9),
            "manual_upload": rules.get("manual_upload_authority_score", 0.6),
            "unknown": rules.get("unknown_authority_score", 0.3),
        }
        
        from app.models.knowledge import KnowledgeSource
        doc_ids = [d.id for d in documents]
        stmt = select(KnowledgeSource).where(KnowledgeSource.document_id.in_(doc_ids))
        res = await db.execute(stmt)
        sources = res.scalars().all()
        
        doc_sources = {src.document_id: src for src in sources}
        
        for doc in documents:
            doc_id = doc.id
            score = type_scores["unknown"]
            
            primary_source = doc_sources.get(doc_id)
            if primary_source:
                source_type = primary_source.source_type
                
                # Check mapping
                if source_type in type_scores:
                    score = type_scores[source_type]
                else:
                    score = getattr(primary_source, "authority_score", 0.5)
            else:
                # If document status / tags indicate internal approved
                doc_tags = {t.name.lower() for t in getattr(doc, "tags", [])}
                if "approved" in doc_tags or "official" in doc_tags:
                    score = type_scores["internal"]
                else:
                    score = type_scores["manual_upload"]
                    
            authority_scores[doc_id] = float(score)

            
        return authority_scores

authority_service = AuthorityService()
