import uuid
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.validation import ValidationAuditLog

class AuditService:
    async def log_audit(
        self,
        db: AsyncSession,
        validator_version: str,
        execution_time_ms: int,
        knowledge_snapshot: Optional[dict],
        retrieved_chunks: Optional[dict],
        validation_rules: Optional[dict],
        results: Optional[dict],
        warnings: Optional[dict],
        errors: Optional[dict],
        user_id: Optional[uuid.UUID] = None
    ) -> ValidationAuditLog:
        """
        Creates an immutable validation audit log entry.
        Never overwrites.
        """
        audit_log = ValidationAuditLog(
            id=uuid.uuid4(),
            validator_version=validator_version,
            execution_time_ms=execution_time_ms,
            knowledge_snapshot=knowledge_snapshot or {},
            retrieved_chunks=retrieved_chunks or {},
            validation_rules=validation_rules or {},
            results=results or {},
            warnings=warnings or {},
            errors=errors or {},
            user_id=user_id
        )
        db.add(audit_log)
        await db.commit()
        await db.refresh(audit_log)
        return audit_log

    async def list_audits(self, db: AsyncSession, limit: int = 50) -> List[ValidationAuditLog]:
        query = select(ValidationAuditLog).order_by(ValidationAuditLog.created_at.desc()).limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

audit_service = AuditService()
