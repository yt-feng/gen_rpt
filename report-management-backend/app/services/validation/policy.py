import uuid
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.models.validation import ValidationPolicy
from app.schemas.validation import ValidationPolicyCreate, ValidationPolicyUpdate
from app.core.config import settings

class PolicyService:
    async def get_active_policy(self, db: AsyncSession) -> ValidationPolicy:
        """Retrieves the currently active policy. If none exists, creates the default one."""
        query = select(ValidationPolicy).where(ValidationPolicy.is_active == True)
        result = await db.execute(query)
        policy = result.scalar_one_or_none()
        
        if not policy:
            policy = await self.create_default_policy(db)
        return policy

    async def create_default_policy(self, db: AsyncSession) -> ValidationPolicy:
        # Load from config settings or hardcoded defaults
        policy_in = ValidationPolicy(
            id=uuid.uuid4(),
            name="Default Enterprise Validation Policy",
            is_active=True,
            min_authority=settings.KNOWLEDGE_VALIDATION_SETTINGS.get("min_authority", 0.5),
            min_freshness=settings.KNOWLEDGE_VALIDATION_SETTINGS.get("min_freshness", 0.5),
            min_confidence=settings.KNOWLEDGE_VALIDATION_SETTINGS.get("min_confidence", 0.5),
            max_duplicate_ratio=settings.KNOWLEDGE_VALIDATION_SETTINGS.get("max_duplicate_ratio", 0.3),
            min_sources=settings.KNOWLEDGE_VALIDATION_SETTINGS.get("min_sources", 2),
            conflict_threshold=settings.KNOWLEDGE_VALIDATION_SETTINGS.get("conflict_threshold", 0.5),
            knowledge_quality_threshold=settings.KNOWLEDGE_VALIDATION_SETTINGS.get("knowledge_quality_threshold", 0.5),
            rules={
                "allowed_source_types": ["government", "research", "internal", "industry_standards", "enterprise_knowledge"],
                "freshness_decay_days": 365,
                "government_authority_score": 1.0,
                "research_authority_score": 0.8,
                "internal_authority_score": 0.9,
                "industry_standards_authority_score": 0.8,
                "manual_upload_authority_score": 0.6,
                "unknown_authority_score": 0.3,
            }
        )
        db.add(policy_in)
        await db.commit()
        await db.refresh(policy_in)
        return policy_in

    async def list_policies(self, db: AsyncSession) -> List[ValidationPolicy]:
        query = select(ValidationPolicy)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_policy(self, db: AsyncSession, policy_id: uuid.UUID) -> Optional[ValidationPolicy]:
        query = select(ValidationPolicy).where(ValidationPolicy.id == policy_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def create_policy(self, db: AsyncSession, obj_in: ValidationPolicyCreate) -> ValidationPolicy:
        db_obj = ValidationPolicy(
            id=uuid.uuid4(),
            **obj_in.model_dump()
        )
        if db_obj.is_active:
            # Deactivate all other policies
            await db.execute(
                update(ValidationPolicy).where(ValidationPolicy.is_active == True).values(is_active=False)
            )
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def update_policy(self, db: AsyncSession, policy_id: uuid.UUID, obj_in: ValidationPolicyUpdate) -> Optional[ValidationPolicy]:
        db_obj = await self.get_policy(db, policy_id)
        if not db_obj:
            return None
        
        update_data = obj_in.model_dump(exclude_unset=True)
        if update_data.get("is_active") is True:
            # Deactivate all other policies
            await db.execute(
                update(ValidationPolicy).where(ValidationPolicy.is_active == True).values(is_active=False)
            )
            
        for key, value in update_data.items():
            setattr(db_obj, key, value)
            
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

policy_service = PolicyService()
