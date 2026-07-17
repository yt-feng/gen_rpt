import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.knowledge import KnowledgeDocument, KnowledgeCollection

class GovernanceService:
    async def get_governance_report(self, db: AsyncSession) -> Dict[str, Any]:
        # Flag docs older than 365 days
        threshold = datetime.now(timezone.utc) - timedelta(days=365)
        
        flagged_res = await db.execute(
            select(KnowledgeDocument).filter(
                KnowledgeDocument.created_at < threshold,
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        flagged_docs = flagged_res.scalars().all()

        total_res = await db.execute(
            select(func.count(KnowledgeDocument.id)).filter(KnowledgeDocument.deleted_at.is_(None))
        )
        total = total_res.scalar() or 0

        compliance_rate = ((total - len(flagged_docs)) / total) if total > 0 else 1.0

        return {
            "policy_compliance_rate": round(compliance_rate, 2),
            "retention_flagged_count": len(flagged_docs),
            "non_compliant_documents": [
                {
                    "document_id": str(doc.id),
                    "file_name": doc.file_name,
                    "created_at": doc.created_at.isoformat(),
                    "reason": "Exceeds 365-day retention policy"
                } for doc in flagged_docs
            ],
            "policies_active": [
                {
                    "name": "Standard Retention Policy",
                    "description": "Retain documents for maximum 365 days",
                    "type": "retention",
                    "threshold_days": 365
                },
                {
                    "name": "Validation SLA Policy",
                    "description": "All documents must be validated within 24 hours of upload",
                    "type": "compliance",
                    "threshold_hours": 24
                }
            ]
        }

governance_service = GovernanceService()
