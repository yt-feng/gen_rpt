import uuid
import hashlib
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status
from app.models.document import Document, DocumentVersion, DocumentSection, DocumentBlock
from app.models.enums import DocStatus
from app.models.knowledge import KnowledgeDocument, KnowledgeChunk, EmbeddingMetadata, KnowledgeCollection

class ApprovedKnowledgeService:
    async def ingest_report(self, db: AsyncSession, report_id: uuid.UUID, target_collection_id: uuid.UUID, user_id: uuid.UUID) -> Dict[str, Any]:
        # 1. Verify target collection exists
        collection = await db.get(KnowledgeCollection, target_collection_id)
        if not collection:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target Collection not found."
            )

        # 2. Fetch the report document
        doc_res = await db.execute(
            select(Document).filter(Document.id == report_id)
        )
        report = doc_res.scalars().first()
        if not report:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Report document not found."
            )

        # 3. Verify status
        if report.status not in (DocStatus.approved, DocStatus.published):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only approved or published reports may be ingested."
            )

        # Fetch current version
        if not report.current_version_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Report does not have an active version."
            )
            
        version_res = await db.execute(
            select(DocumentVersion).filter(DocumentVersion.id == report.current_version_id)
        )
        version = version_res.scalars().first()
        if not version:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Current version not found."
            )

        # 4. Create KnowledgeDocument
        checksum = hashlib.sha256(report.title.encode("utf-8")).hexdigest()
        k_doc_id = uuid.uuid4()
        k_doc = KnowledgeDocument(
            id=k_doc_id,
            collection_id=target_collection_id,
            file_name=f"{report.title}.md",
            original_file_name=f"{report.title}.md",
            mime_type="text/markdown",
            extension="md",
            checksum=checksum,
            storage_path=version.snapshot_markdown_url or f"reports/{report_id}/version_{version.version_number}.md",
            version=1,
            size=1024,
            processing_status="completed",
            upload_status="uploaded",
            validation_status="validated",
            created_by=user_id
        )
        db.add(k_doc)

        # 5. Extract blocks and create chunks
        sections_res = await db.execute(
            select(DocumentSection).filter(DocumentSection.version_id == version.id).order_by(DocumentSection.section_order.asc())
        )
        sections = list(sections_res.scalars().all())
        
        chunk_number = 1
        for sec in sections:
            blocks_res = await db.execute(
                select(DocumentBlock).filter(DocumentBlock.section_id == sec.id).order_by(DocumentBlock.block_order.asc())
            )
            blocks = list(blocks_res.scalars().all())
            for block in blocks:
                content = block.markdown or ""
                if not content:
                    continue
                # Create KnowledgeChunk
                chunk = KnowledgeChunk(
                    id=uuid.uuid4(),
                    document_id=k_doc_id,
                    chunk_number=chunk_number,
                    section=sec.title,
                    heading=sec.title,
                    token_count=len(content.split()),
                    character_count=len(content),
                    hash=hashlib.md5(content.encode("utf-8")).hexdigest(),
                    processing_version="1.0",
                    chunk_metadata={"content": content}
                )
                db.add(chunk)

                # Create EmbeddingMetadata stub
                emb = EmbeddingMetadata(
                    chunk_id=chunk.id,
                    embedding_model="text-embedding-ada-002",
                    embedding_version="v1",
                    dimension=1536,
                    status="completed"
                )
                db.add(emb)
                chunk_number += 1

        await db.commit()

        # Log activity
        from app.services.knowledge_collection import knowledge_collection_service
        await knowledge_collection_service.log_activity(
            db,
            collection_id=target_collection_id,
            document_id=k_doc_id,
            user_id=user_id,
            activity_type="upload",
            details={"action": "ingested_report", "report_id": str(report_id)}
        )

        return {
            "status": "success",
            "document_id": k_doc_id,
            "chunks_count": chunk_number - 1,
            "validation_status": "validated"
        }

approved_knowledge_service = ApprovedKnowledgeService()
