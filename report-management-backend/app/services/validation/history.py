import uuid
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.validation import ValidationHistory

class HistoryService:
    async def log_history_run(
        self,
        db: AsyncSession,
        session_id: Optional[uuid.UUID],
        validation_run_id: uuid.UUID,
        knowledge_version: str,
        validation_policy_id: Optional[uuid.UUID],
        confidence_score: float,
        conflict_count: int,
        freshness_score: float,
        details: Optional[dict] = None
    ) -> ValidationHistory:
        """
        Creates an immutable validation run history record.
        Never overwrites.
        """
        history_rec = ValidationHistory(
            id=uuid.uuid4(),
            session_id=session_id,
            validation_run_id=validation_run_id,
            knowledge_version=knowledge_version,
            validation_policy_id=validation_policy_id,
            confidence_score=confidence_score,
            conflict_count=conflict_count,
            freshness_score=freshness_score,
            details=details or {}
        )
        db.add(history_rec)
        await db.commit()
        await db.refresh(history_rec)
        return history_rec

    async def get_history_by_session(self, db: AsyncSession, session_id: uuid.UUID) -> List[ValidationHistory]:
        query = select(ValidationHistory).where(ValidationHistory.session_id == session_id).order_by(ValidationHistory.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_history_by_run(self, db: AsyncSession, run_id: uuid.UUID) -> Optional[ValidationHistory]:
        query = select(ValidationHistory).where(ValidationHistory.validation_run_id == run_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def list_history(self, db: AsyncSession, limit: int = 50) -> List[ValidationHistory]:
        query = select(ValidationHistory).order_by(ValidationHistory.created_at.desc()).limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

history_service = HistoryService()
