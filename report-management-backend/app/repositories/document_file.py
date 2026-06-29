from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.models.document import DocumentFile
from app.schemas.document import DocumentFileCreate, DocumentUpdate
from app.repositories.base import BaseRepository

class DocumentFileRepository(BaseRepository[DocumentFile, DocumentFileCreate, DocumentUpdate]):
    async def get_by_path(self, db: AsyncSession, *, path: str) -> Optional[DocumentFile]:
        result = await db.execute(select(DocumentFile).filter(DocumentFile.storage_path == path))
        return result.scalars().first()

    async def get_by_version(self, db: AsyncSession, *, version_id: UUID) -> list[DocumentFile]:
        result = await db.execute(select(DocumentFile).filter(DocumentFile.version_id == version_id))
        return list(result.scalars().all())

document_file_repo = DocumentFileRepository(DocumentFile)
