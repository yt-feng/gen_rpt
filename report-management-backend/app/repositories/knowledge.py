from typing import List, Optional, Any, Dict
from uuid import UUID
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.base import BaseRepository
from app.models.knowledge import (
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeSource,
    KnowledgeCategory,
    KnowledgeTag,
    KnowledgeChunk,
    EmbeddingMetadata,
    RetrievalSession,
    RetrievalResult,
    ValidationResult,
    KnowledgeRelationship,
    KnowledgeProcessingQueue,
    KnowledgeActivityHistory,
    CollectionPermission,
    KnowledgeAnalytics,
    KnowledgeVersionHistory,
    KnowledgeSynchronizationLog,
    KnowledgeProcessingAuditLog
)
from app.schemas.knowledge import (
    CollectionCreate,
    CollectionUpdate,
    DocumentCreate,
    DocumentUpdate,
    SourceCreate,
    ChunkCreate,
    PermissionCreate,
    ProcessingJobCreate,
    ValidationRequest
)

# ==========================================
# Repositories Implementation
# ==========================================

class CollectionRepository(BaseRepository[KnowledgeCollection, CollectionCreate, CollectionUpdate]):
    async def get_by_slug(self, db: AsyncSession, slug: str) -> Optional[KnowledgeCollection]:
        result = await db.execute(select(self.model).filter(self.model.slug == slug, self.model.deleted_at.is_(None)))
        return result.scalars().first()

    async def list_active_collections(self, db: AsyncSession, owner_id: UUID) -> List[KnowledgeCollection]:
        from sqlalchemy import or_
        result = await db.execute(
            select(self.model).filter(
                or_(
                    self.model.owner_id == owner_id,
                    self.model.visibility.in_(["public", "shared"])
                ),
                self.model.deleted_at.is_(None)
            )
        )
        return list(result.scalars().all())


class DocumentRepository(BaseRepository[KnowledgeDocument, DocumentCreate, DocumentUpdate]):
    async def get_by_checksum(self, db: AsyncSession, checksum: str) -> Optional[KnowledgeDocument]:
        result = await db.execute(select(self.model).filter(self.model.checksum == checksum, self.model.deleted_at.is_(None)))
        return result.scalars().first()

    async def list_by_collection(self, db: AsyncSession, collection_id: UUID) -> List[KnowledgeDocument]:
        result = await db.execute(
            select(self.model).filter(
                self.model.collection_id == collection_id,
                self.model.deleted_at.is_(None)
            )
        )
        return list(result.scalars().all())


class SourceRepository(BaseRepository[KnowledgeSource, SourceCreate, Any]):
    async def get_by_document(self, db: AsyncSession, document_id: UUID) -> List[KnowledgeSource]:
        result = await db.execute(select(self.model).filter(self.model.document_id == document_id))
        return list(result.scalars().all())


class ChunkRepository(BaseRepository[KnowledgeChunk, ChunkCreate, Any]):
    async def get_by_document_and_number(self, db: AsyncSession, document_id: UUID, chunk_number: int) -> Optional[KnowledgeChunk]:
        result = await db.execute(
            select(self.model).filter(
                self.model.document_id == document_id,
                self.model.chunk_number == chunk_number
            )
        )
        return result.scalars().first()

    async def list_by_document(self, db: AsyncSession, document_id: UUID) -> List[KnowledgeChunk]:
        result = await db.execute(
            select(self.model).filter(self.model.document_id == document_id).order_by(self.model.chunk_number.asc())
        )
        return list(result.scalars().all())


class RetrievalRepository(BaseRepository[RetrievalSession, Any, Any]):
    async def get_session_by_job(self, db: AsyncSession, generation_job_id: UUID) -> Optional[RetrievalSession]:
        result = await db.execute(select(self.model).filter(self.model.generation_job_id == generation_job_id))
        return result.scalars().first()


class ValidationRepository(BaseRepository[ValidationResult, ValidationRequest, Any]):
    async def list_by_session(self, db: AsyncSession, session_id: UUID) -> List[ValidationResult]:
        result = await db.execute(select(self.model).filter(self.model.session_id == session_id))
        return list(result.scalars().all())

    async def list_by_document(self, db: AsyncSession, document_id: UUID) -> List[ValidationResult]:
        result = await db.execute(select(self.model).filter(self.model.document_id == document_id))
        return list(result.scalars().all())


class QueueRepository(BaseRepository[KnowledgeProcessingQueue, ProcessingJobCreate, Any]):
    async def list_active_jobs(self, db: AsyncSession) -> List[KnowledgeProcessingQueue]:
        result = await db.execute(
            select(self.model).filter(self.model.status.in_(["pending", "running"]))
        )
        return list(result.scalars().all())


class PermissionRepository(BaseRepository[CollectionPermission, PermissionCreate, Any]):
    async def get_user_permission(self, db: AsyncSession, collection_id: UUID, user_id: UUID) -> Optional[CollectionPermission]:
        result = await db.execute(
            select(self.model).filter(
                self.model.collection_id == collection_id,
                self.model.user_id == user_id
            )
        )
        return result.scalars().first()


class AnalyticsRepository(BaseRepository[KnowledgeAnalytics, Any, Any]):
    async def get_latest_analytics(self, db: AsyncSession, collection_id: Optional[UUID] = None) -> Optional[KnowledgeAnalytics]:
        query = select(self.model)
        if collection_id:
            query = query.filter(self.model.collection_id == collection_id)
        result = await db.execute(query.order_by(self.model.recorded_date.desc()))
        return result.scalars().first()


# Singletons instantiated
collection_repo = CollectionRepository(KnowledgeCollection)
document_repo = DocumentRepository(KnowledgeDocument)
source_repo = SourceRepository(KnowledgeSource)
chunk_repo = ChunkRepository(KnowledgeChunk)
retrieval_repo = RetrievalRepository(RetrievalSession)
validation_repo = ValidationRepository(ValidationResult)
queue_repo = QueueRepository(KnowledgeProcessingQueue)
permission_repo = PermissionRepository(CollectionPermission)
analytics_repo = AnalyticsRepository(KnowledgeAnalytics)
