import uuid
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.knowledge import KnowledgeActivityHistory

class AuditService:
    async def get_audit_logs(self, db: AsyncSession) -> Dict[str, Any]:
        # Query active activity logs
        logs_res = await db.execute(
            select(KnowledgeActivityHistory).order_by(KnowledgeActivityHistory.created_at.desc()).limit(50)
        )
        logs = logs_res.scalars().all()

        return {
            "logs": [
                {
                    "audit_id": str(log.id),
                    "collection_id": str(log.collection_id) if log.collection_id else None,
                    "document_id": str(log.document_id) if log.document_id else None,
                    "user_id": str(log.user_id) if log.user_id else None,
                    "activity_type": log.activity_type,
                    "details": log.details,
                    "created_at": log.created_at.isoformat()
                } for log in logs
            ]
        }

audit_service = AuditService()
