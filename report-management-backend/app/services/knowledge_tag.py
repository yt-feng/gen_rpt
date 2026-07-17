import uuid
from typing import List, Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func, insert
from fastapi import HTTPException, status

from app.models.knowledge import KnowledgeTag, KnowledgeDocument, knowledge_document_tags
from app.schemas.knowledge import TagCreate
from app.services.knowledge_cache import knowledge_cache_service

class KnowledgeTagService:
    async def create_tag(self, db: AsyncSession, obj_in: TagCreate) -> KnowledgeTag:
        # Check uniqueness
        existing_res = await db.execute(
            select(KnowledgeTag).filter(
                (KnowledgeTag.name == obj_in.name) | (KnowledgeTag.slug == obj_in.slug)
            )
        )
        if existing_res.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Tag with name '{obj_in.name}' or slug '{obj_in.slug}' already exists."
            )
        tag = KnowledgeTag(name=obj_in.name, slug=obj_in.slug)
        db.add(tag)
        await db.commit()
        await db.refresh(tag)
        await knowledge_cache_service.invalidate_tags()
        return tag

    async def get_tag(self, db: AsyncSession, tag_id: uuid.UUID) -> Optional[KnowledgeTag]:
        return await db.get(KnowledgeTag, tag_id)

    async def list_tags(self, db: AsyncSession) -> List[KnowledgeTag]:
        cache_key = "tags:list"
        cached = await knowledge_cache_service.get(cache_key)
        if cached is not None:
            return cached
            
        res = await db.execute(select(KnowledgeTag).order_by(KnowledgeTag.name.asc()))
        tags = list(res.scalars().all())
        await knowledge_cache_service.set(cache_key, tags)
        return tags

    async def delete_tag(self, db: AsyncSession, tag_id: uuid.UUID) -> bool:
        tag = await self.get_tag(db, tag_id)
        if not tag:
            return False
        await db.delete(tag)
        await db.commit()
        await knowledge_cache_service.invalidate_tags()
        return True

    async def assign_tag_to_document(self, db: AsyncSession, document_id: uuid.UUID, tag_id: uuid.UUID) -> bool:
        # Check if already assigned
        check_res = await db.execute(
            select(1).select_from(knowledge_document_tags).filter(
                knowledge_document_tags.c.document_id == document_id,
                knowledge_document_tags.c.tag_id == tag_id
            )
        )
        if check_res.scalar():
            return True
            
        await db.execute(
            insert(knowledge_document_tags).values(document_id=document_id, tag_id=tag_id)
        )
        await db.commit()
        await knowledge_cache_service.invalidate_tags()
        return True

    async def unassign_tag_from_document(self, db: AsyncSession, document_id: uuid.UUID, tag_id: uuid.UUID) -> bool:
        await db.execute(
            delete(knowledge_document_tags).where(
                knowledge_document_tags.c.document_id == document_id,
                knowledge_document_tags.c.tag_id == tag_id
            )
        )
        await db.commit()
        await knowledge_cache_service.invalidate_tags()
        return True

    async def merge_tags(self, db: AsyncSession, source_tag_id: uuid.UUID, target_tag_id: uuid.UUID) -> bool:
        # 1. Fetch assigned documents from source tag
        docs_res = await db.execute(
            select(knowledge_document_tags.c.document_id).filter(
                knowledge_document_tags.c.tag_id == source_tag_id
            )
        )
        source_doc_ids = docs_res.scalars().all()
        
        # 2. Re-assign each document to target tag if not already assigned
        for doc_id in source_doc_ids:
            check_res = await db.execute(
                select(1).select_from(knowledge_document_tags).filter(
                    knowledge_document_tags.c.document_id == doc_id,
                    knowledge_document_tags.c.tag_id == target_tag_id
                )
            )
            if not check_res.scalar():
                await db.execute(
                    insert(knowledge_document_tags).values(document_id=doc_id, tag_id=target_tag_id)
                )
                
        # 3. Clean up references to source tag
        await db.execute(
            delete(knowledge_document_tags).where(knowledge_document_tags.c.tag_id == source_tag_id)
        )
        
        # 4. Delete source tag
        await db.execute(delete(KnowledgeTag).where(KnowledgeTag.id == source_tag_id))
        await db.commit()
        
        await knowledge_cache_service.invalidate_tags()
        return True

    async def get_tag_statistics(self, db: AsyncSession) -> Dict[str, int]:
        cache_key = "tags:statistics"
        cached = await knowledge_cache_service.get(cache_key)
        if cached is not None:
            return cached
            
        res = await db.execute(
            select(KnowledgeTag.name, func.count(knowledge_document_tags.c.document_id))
            .select_from(KnowledgeTag)
            .outerjoin(knowledge_document_tags, KnowledgeTag.id == knowledge_document_tags.c.tag_id)
            .group_by(KnowledgeTag.name)
        )
        stats = {row[0]: row[1] for row in res.all()}
        await knowledge_cache_service.set(cache_key, stats)
        return stats

knowledge_tag_service = KnowledgeTagService()
