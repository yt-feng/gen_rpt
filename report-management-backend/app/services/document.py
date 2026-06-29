from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.document import document_repo, document_version_repo
from app.schemas.document import DocumentCreate, DocumentVersionCreate
from app.models.document import Document
from app.models.enums import DocChangeType, DocStatus

class DocumentService:
    async def create_document(self, db: AsyncSession, doc_in: DocumentCreate, user_id: UUID) -> Document:
        # Create Document
        document = await document_repo.create(db=db, obj_in=doc_in)
        document.created_by = user_id
        await db.commit()
        await db.refresh(document)
        
        # Create Initial Version
        version_in = DocumentVersionCreate(
            document_id=document.id,
            version_number=1,
            change_type=DocChangeType.HUMAN_EDIT,
            summary="Initial creation",
            status=DocStatus.draft
        )
        version = await document_version_repo.create(db=db, obj_in=version_in)
        version.created_by = user_id
        
        # Link current version
        document.current_version_id = version.id
        await db.commit()
        await db.refresh(document)
        
        return document

document_service = DocumentService()
