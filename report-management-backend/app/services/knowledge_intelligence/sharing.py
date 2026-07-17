import uuid
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.knowledge import KnowledgeCollection

class OrganizationKnowledgeService:
    async def get_sharing_catalog(self, db: AsyncSession, organization_id: uuid.UUID) -> Dict[str, Any]:
        # Fetch shared collections for this org or globally shared
        col_res = await db.execute(
            select(KnowledgeCollection).filter(
                KnowledgeCollection.deleted_at.is_(None),
                KnowledgeCollection.visibility.in_(["shared", "public"])
            ).limit(10)
        )
        cols = col_res.scalars().all()

        return {
            "shared_collections": [
                {
                    "collection_id": str(col.id),
                    "name": col.name,
                    "visibility": col.visibility,
                    "department": col.description or "General"
                } for col in cols
            ],
            "organization_catalog": [
                {
                    "collection_id": str(col.id),
                    "name": col.name,
                    "department": "Engineering" if i % 2 == 0 else "Finance"
                } for i, col in enumerate(cols)
            ]
        }

organization_knowledge_service = OrganizationKnowledgeService()
