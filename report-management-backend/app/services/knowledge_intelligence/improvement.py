import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.knowledge import KnowledgeDocument

class ContinuousImprovementService:
    async def get_improvement_suggestions(self, db: AsyncSession) -> Dict[str, Any]:
        # Stale documents older than 180 days
        stale_threshold = datetime.now(timezone.utc) - timedelta(days=180)
        stale_res = await db.execute(
            select(KnowledgeDocument).filter(
                KnowledgeDocument.updated_at < stale_threshold,
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        stale_docs = stale_res.scalars().all()

        # Duplicate detection using matching checksums (group by checksum having count > 1)
        subquery = (
            select(KnowledgeDocument.checksum)
            .filter(KnowledgeDocument.deleted_at.is_(None))
            .group_by(KnowledgeDocument.checksum)
            .having(func.count(KnowledgeDocument.id) > 1)
            .subquery()
        )
        dup_res = await db.execute(
            select(KnowledgeDocument).filter(
                KnowledgeDocument.checksum.in_(select(subquery))
            )
        )
        dup_docs = dup_res.scalars().all()

        suggestions = []
        if stale_docs:
            suggestions.append({
                "type": "retire_stale",
                "suggestion": f"Retire or archive {len(stale_docs)} document(s) older than 180 days.",
                "impact": "high"
            })
        if dup_docs:
            suggestions.append({
                "type": "resolve_duplicates",
                "suggestion": f"Consolidate duplicate document(s) with identical checksums.",
                "impact": "medium"
            })

        return {
            "stale_documents": [
                {
                    "document_id": str(doc.id),
                    "file_name": doc.file_name,
                    "last_updated": doc.updated_at.isoformat(),
                    "days_inactive": (datetime.now(timezone.utc) - doc.updated_at).days
                } for doc in stale_docs
            ],
            "duplicate_documents": [
                {
                    "document_id": str(doc.id),
                    "file_name": doc.file_name,
                    "checksum": doc.checksum
                } for doc in dup_docs
            ],
            "gap_documents": [],
            "suggestions": suggestions
        }

continuous_improvement_service = ContinuousImprovementService()
