import uuid
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.models.knowledge import KnowledgeDocument, KnowledgeCollection, knowledge_document_tags
from app.schemas.knowledge import SearchRequest

class KnowledgeSearchService:
    async def search_repository_metadata(
        self, db: AsyncSession, payload: SearchRequest, user_org_id: Optional[uuid.UUID] = None
    ) -> List[KnowledgeDocument]:
        # Formulate query
        query = select(KnowledgeDocument).join(
            KnowledgeCollection, KnowledgeDocument.collection_id == KnowledgeCollection.id
        ).filter(
            KnowledgeDocument.deleted_at.is_(None)
        )
        
        # Enforce multi-tenancy organization isolation
        if user_org_id:
            query = query.filter(KnowledgeCollection.organization_id == user_org_id)
            
        filters = []
        
        # Keyword query filter
        if payload.query and payload.query.strip():
            keyword = f"%{payload.query.strip()}%"
            filters.append(
                or_(
                    KnowledgeDocument.file_name.ilike(keyword),
                    KnowledgeDocument.original_file_name.ilike(keyword),
                    KnowledgeDocument.mime_type.ilike(keyword),
                    KnowledgeDocument.language.ilike(keyword)
                )
            )
            
        # Target collection filtering
        if payload.collection_id:
            filters.append(KnowledgeDocument.collection_id == payload.collection_id)
            
        # Format (document_type) filtering
        if payload.document_type:
            filters.append(KnowledgeDocument.mime_type.ilike(f"%{payload.document_type}%"))
            
        # Status filtering
        if payload.processing_status:
            filters.append(KnowledgeDocument.processing_status == payload.processing_status)
            
        if payload.validation_status:
            filters.append(KnowledgeDocument.validation_status == payload.validation_status)
            
        # Language filters
        if payload.languages:
            filters.append(KnowledgeDocument.language.in_(payload.languages))
            
        # Tags many-to-many filtering
        if payload.tags:
            tag_subquery = select(knowledge_document_tags.c.document_id).filter(
                knowledge_document_tags.c.tag_id.in_(payload.tags)
            )
            filters.append(KnowledgeDocument.id.in_(tag_subquery))
            
        if filters:
            query = query.filter(and_(*filters))
            
        query = query.order_by(KnowledgeDocument.created_at.desc()).limit(payload.limit)
        
        res = await db.execute(query)
        # Load relationships eagerly if needed, or SQLAlchemy default
        return list(res.scalars().all())

knowledge_search_service = KnowledgeSearchService()
