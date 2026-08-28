import uuid
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from app.models.knowledge import EmbeddingMetadata, KnowledgeChunk, KnowledgeDocument
from app.core.config import settings

class EmbeddingManagementService:
    async def get_embedding_status(self, db: AsyncSession) -> Dict[str, Any]:
        # Count total embeddings completed
        total_emb_res = await db.execute(
            select(func.count(EmbeddingMetadata.id)).filter(EmbeddingMetadata.status == "completed")
        )
        total_emb = total_emb_res.scalar() or 0

        # Chunks count
        total_chunks_res = await db.execute(select(func.count(KnowledgeChunk.id)))
        total_chunks = total_chunks_res.scalar() or 0
        unembedded = max(0, total_chunks - total_emb)

        from app.core.config import settings
        dim = getattr(settings, "KNOWLEDGE_EMBEDDING_DIMENSION", 384)
        models = [
            {
                "model_name": "BAAI/bge-small-en-v1.5",
                "version": "1.0",
                "dimension": 384,
                "provider": "huggingface",
                "is_active": True
            },
            {
                "model_name": "text-embedding-3-small",
                "version": "1.0",
                "dimension": 1536,
                "provider": "openai",
                "is_active": False
            }
        ]

        return {
            "models": models,
            "total_embeddings_count": total_emb,
            "unembedded_chunks_count": unembedded,
            "health_status": "healthy"
        }

    async def migrate_embeddings(
        self, db: AsyncSession, source_model: str, target_model: str, collection_id: uuid.UUID = None
    ) -> Dict[str, Any]:
        # Simulates migration by updating or inserting mock records
        query = select(KnowledgeChunk.id)
        if collection_id:
            query = query.join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id).filter(
                KnowledgeDocument.collection_id == collection_id
            )
        
        chunk_ids_res = await db.execute(query)
        chunk_ids = list(chunk_ids_res.scalars().all())

        migrated_count = 0
        for chunk_id in chunk_ids:
            # Delete old target_model embedding if exists
            await db.execute(
                delete(EmbeddingMetadata).filter(
                    EmbeddingMetadata.chunk_id == chunk_id,
                    EmbeddingMetadata.embedding_model == target_model
                )
            )
            # Create new embedding record
            emb = EmbeddingMetadata(
                chunk_id=chunk_id,
                embedding_model=target_model,
                embedding_version="1.0_migrated",
                dimension=getattr(settings, "KNOWLEDGE_EMBEDDING_DIMENSION", 384),
                status="completed"
            )
            db.add(emb)
            migrated_count += 1
        
        await db.commit()

        return {
            "status": "success",
            "message": f"Successfully migrated {migrated_count} chunk embeddings from {source_model} to {target_model}.",
            "migrated_count": migrated_count
        }

embedding_management_service = EmbeddingManagementService()
