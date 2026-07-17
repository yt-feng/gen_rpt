import uuid
import math
from datetime import datetime, timezone
from typing import Dict, Any, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.knowledge import KnowledgeDocument
from app.models.validation import ValidationPolicy


class FreshnessService:
    async def calculate_freshness(
        self,
        db: AsyncSession,
        documents: List[KnowledgeDocument],
        policy: ValidationPolicy
    ) -> Dict[uuid.UUID, float]:
        """
        Calculates freshness score for each document/source based on policy guidelines.
        """
        rules = policy.rules or {}
        decay_days = rules.get("freshness_decay_days", 365)
        now = datetime.now(timezone.utc)
        freshness_scores = {}
        
        from app.models.knowledge import KnowledgeSource
        doc_ids = [d.id for d in documents]
        stmt = select(KnowledgeSource).where(KnowledgeSource.document_id.in_(doc_ids))
        res = await db.execute(stmt)
        sources = res.scalars().all()
        
        doc_sources = {src.document_id: src for src in sources}
        
        for doc in documents:
            doc_id = doc.id
            
            # Determine base timestamp: publication_date > created_at
            pub_date = None
            primary_source = doc_sources.get(doc_id)
            if primary_source:
                pub_date = primary_source.publication_date
                
            base_date = pub_date or doc.created_at

            
            # Make timezone aware if it is naive
            if base_date.tzinfo is None:
                base_date = base_date.replace(tzinfo=timezone.utc)
                
            age_days = (now - base_date).days
            if age_days < 0:
                age_days = 0
                
            # Score decay calculation (linear or exponential)
            decay_type = rules.get("freshness_decay_type", "linear")
            if decay_type == "exponential":
                # exp(-lambda * x) where lambda is chosen such that exp(-lambda * decay_days) = 0.5
                rate = 0.693 / max(1, decay_days)
                score = math.exp(-rate * age_days)
            else:
                score = max(0.1, 1.0 - (age_days / max(1, decay_days)))
                
            freshness_scores[doc_id] = float(round(score, 4))
            
        return freshness_scores

# Instantiate service singleton
freshness_service = FreshnessService()
