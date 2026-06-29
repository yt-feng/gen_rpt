from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from uuid import UUID

from app.models.document import Document, DocumentVersion, DocumentSection, DocumentBlock
from app.schemas.document import DocumentCreate, DocumentUpdate
from app.repositories.base import BaseRepository

class DocumentRepository(BaseRepository[Document, DocumentCreate, DocumentUpdate]):
    async def get_by_slug(self, db: AsyncSession, *, slug: str) -> Optional[Document]:
        result = await db.execute(
            select(Document).filter(Document.slug == slug)
        )
        return result.scalars().first()
        
    async def get_with_versions(self, db: AsyncSession, *, id: UUID) -> Optional[Document]:
        result = await db.execute(
            select(Document)
            .options(selectinload(Document.versions))
            .filter(Document.id == id)
        )
        return result.scalars().first()

class DocumentVersionRepository(BaseRepository[DocumentVersion, None, None]):
    async def get_with_sections(self, db: AsyncSession, *, id: UUID) -> Optional[DocumentVersion]:
        result = await db.execute(
            select(DocumentVersion)
            .options(
                selectinload(DocumentVersion.sections)
                .selectinload(DocumentSection.blocks)
            )
            .filter(DocumentVersion.id == id)
        )
        return result.scalars().first()

    async def get_by_document(self, db: AsyncSession, *, document_id: UUID) -> List[DocumentVersion]:
        result = await db.execute(
            select(DocumentVersion)
            .filter(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number.desc())
        )
        return list(result.scalars().all())

document_repo = DocumentRepository(Document)
document_version_repo = DocumentVersionRepository(DocumentVersion)
