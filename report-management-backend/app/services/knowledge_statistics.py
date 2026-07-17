import uuid
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.knowledge import KnowledgeDocument, KnowledgeChunk, KnowledgeProcessingQueue
from app.schemas.knowledge import CollectionStatisticsResponse
from app.services.knowledge_cache import knowledge_cache_service

class KnowledgeStatisticsService:
    async def get_collection_statistics(
        self, db: AsyncSession, collection_id: uuid.UUID
    ) -> CollectionStatisticsResponse:
        cache_key = f"stats:collection:{collection_id}"
        cached = await knowledge_cache_service.get(cache_key)
        if cached is not None:
            return cached
            
        # Document Count & Size
        doc_stats_res = await db.execute(
            select(
                func.count(KnowledgeDocument.id),
                func.sum(KnowledgeDocument.size)
            )
            .filter(
                KnowledgeDocument.collection_id == collection_id,
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        doc_count, total_size = doc_stats_res.first()
        doc_count = doc_count or 0
        total_size = total_size or 0
        
        # Chunk Count
        chunk_count_res = await db.execute(
            select(func.count(KnowledgeChunk.id))
            .select_from(KnowledgeChunk)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .filter(
                KnowledgeDocument.collection_id == collection_id,
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        chunk_count = chunk_count_res.scalar() or 0
        
        # Language Distribution
        lang_res = await db.execute(
            select(KnowledgeDocument.language, func.count(KnowledgeDocument.id))
            .filter(
                KnowledgeDocument.collection_id == collection_id,
                KnowledgeDocument.deleted_at.is_(None)
            )
            .group_by(KnowledgeDocument.language)
        )
        language_dist = {row[0] or "unknown": row[1] for row in lang_res.all()}
        
        # Validation Summary
        val_res = await db.execute(
            select(KnowledgeDocument.validation_status, func.count(KnowledgeDocument.id))
            .filter(
                KnowledgeDocument.collection_id == collection_id,
                KnowledgeDocument.deleted_at.is_(None)
            )
            .group_by(KnowledgeDocument.validation_status)
        )
        validation_summary = {row[0] or "pending": row[1] for row in val_res.all()}
        
        stats = CollectionStatisticsResponse(
            collection_id=collection_id,
            document_count=doc_count,
            chunk_count=chunk_count,
            total_size_bytes=total_size,
            language_distribution=language_dist,
            tag_distribution={},        # Placeholders
            category_distribution={},   # Placeholders
            validation_summary=validation_summary
        )
        
        await knowledge_cache_service.set(cache_key, stats)
        return stats

    async def get_global_statistics(self, db: AsyncSession) -> Dict[str, Any]:
        cache_key = "stats:global"
        cached = await knowledge_cache_service.get(cache_key)
        if cached is not None:
            return cached
            
        doc_res = await db.execute(
            select(func.count(KnowledgeDocument.id), func.sum(KnowledgeDocument.size))
            .filter(KnowledgeDocument.deleted_at.is_(None))
        )
        doc_count, total_size = doc_res.first()
        
        chunk_res = await db.execute(select(func.count(KnowledgeChunk.id)))
        chunk_count = chunk_res.scalar() or 0
        
        queue_res = await db.execute(
            select(KnowledgeProcessingQueue.status, func.count(KnowledgeProcessingQueue.id))
            .group_by(KnowledgeProcessingQueue.status)
        )
        queue_backlog = {row[0]: row[1] for row in queue_res.all()}
        
        stats = {
            "document_count": doc_count or 0,
            "total_size_bytes": total_size or 0,
            "chunk_count": chunk_count,
            "queue_backlog": queue_backlog
        }
        await knowledge_cache_service.set(cache_key, stats, ttl=60)
        return stats

knowledge_statistics_service = KnowledgeStatisticsService()
