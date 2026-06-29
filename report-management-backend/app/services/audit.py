import uuid
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.system import AuditLog

class AuditService:
    @staticmethod
    async def log_action(
        db: AsyncSession,
        table_name: str,
        record_id: uuid.UUID,
        action: str,
        old_data: Optional[dict] = None,
        new_data: Optional[dict] = None,
        changed_by: Optional[uuid.UUID] = None
    ) -> AuditLog:
        """
        Creates an immutable audit record for tracking system changes.
        This must be called within an active transaction.
        """
        log_entry = AuditLog(
            table_name=table_name,
            record_id=record_id,
            action=action,
            old_data=old_data,
            new_data=new_data,
            changed_by=changed_by
        )
        db.add(log_entry)
        # We deliberately do not commit here. The caller's transaction context will commit it.
        return log_entry

audit_service = AuditService()
