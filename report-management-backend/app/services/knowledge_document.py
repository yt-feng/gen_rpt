import uuid
import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status, UploadFile

from app.models.knowledge import (
    KnowledgeDocument,
    KnowledgeVersionHistory,
    KnowledgeProcessingQueue,
    KnowledgeCollection,
    KnowledgeActivityHistory
)
from app.services.knowledge_storage import knowledge_storage_service
from app.repositories.knowledge import document_repo

ALLOWED_EXTENSIONS = {".pdf", ".md", ".docx", ".txt", ".html"}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "text/markdown",
    "text/plain",
    "text/html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}

class KnowledgeDocumentService:
    def validate_file(self, filename: str, content_type: str, file_size: int) -> Tuple[bool, str]:
        """
        Task 3: File Validation
        """
        if file_size <= 0:
            return False, "File is empty."

        # Verify extension
        ext = os.path.splitext(filename.lower())[1]
        if ext not in ALLOWED_EXTENSIONS:
            return False, f"Unsupported file extension '{ext}'."

        # Verify MIME type
        if content_type not in ALLOWED_MIME_TYPES:
            return False, f"Unsupported MIME type '{content_type}'."

        # Validate file size (e.g. limit to 50MB)
        MAX_SIZE = 50 * 1024 * 1024 # 50MB
        if file_size > MAX_SIZE:
            return False, f"File size exceeds maximum limit of 50MB."

        return True, ""

    async def upload_document(
        self,
        db: AsyncSession,
        collection_id: uuid.UUID,
        filename: str,
        content_type: str,
        user_id: uuid.UUID,
        file_data: Optional[bytes] = None,
        file_stream: Optional[Any] = None,
        file_size: int = 0,
        duplicate_strategy: str = "skip"  # skip, new_version
    ) -> Dict[str, Any]:
        """
        Task 2, 4, 5, 6, 7, 13, 15: Upload Single Document Flow with transaction rollback and R2 cleanup.
        """
        # Validate collection exists
        collection_res = await db.execute(
            select(KnowledgeCollection).filter(
                KnowledgeCollection.id == collection_id,
                KnowledgeCollection.deleted_at.is_(None)
            )
        )
        collection = collection_res.scalars().first()
        if not collection:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Collection not found."
            )

        # Validate file properties
        if file_data is not None:
            file_size = len(file_data)
        is_valid, err_msg = self.validate_file(filename, content_type, file_size)
        if not is_valid:
            # Audit failed validation
            from app.services.knowledge_collection import knowledge_collection_service
            await knowledge_collection_service.log_activity(
                db,
                collection_id=collection_id,
                user_id=user_id,
                activity_type="validation",
                details={"filename": filename, "error": err_msg, "status": "failed"}
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=err_msg
            )

        # Compute Checksum
        if file_stream is not None:
            import hashlib
            hasher = hashlib.sha256()
            while chunk := file_stream.read(8192):
                hasher.update(chunk)
            checksum = hasher.hexdigest()
            file_stream.seek(0)
        else:
            checksum = knowledge_storage_service.calculate_checksum(file_data)

        # Task 4: Duplicate Detection
        existing_doc_res = await db.execute(
            select(KnowledgeDocument).filter(
                KnowledgeDocument.collection_id == collection_id,
                KnowledgeDocument.checksum == checksum,
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        existing_doc = existing_doc_res.scalars().first()

        if existing_doc:
            if duplicate_strategy == "skip":
                # Create duplicate history log
                from app.services.knowledge_collection import knowledge_collection_service
                await knowledge_collection_service.log_activity(
                    db,
                    collection_id=collection_id,
                    document_id=existing_doc.id,
                    user_id=user_id,
                    activity_type="upload",
                    details={"filename": filename, "checksum": checksum, "status": "duplicate_skipped"}
                )
                return {
                    "status": "skipped",
                    "message": "Duplicate document checksum found in this collection.",
                    "document_id": existing_doc.id
                }
            elif duplicate_strategy == "new_version":
                # Create a new version of the existing document
                new_version_num = existing_doc.version + 1
                ext = os.path.splitext(filename.lower())[1]
                storage_path = knowledge_storage_service.generate_document_path(
                    collection_id, existing_doc.id, filename, version=new_version_num
                )

                # Attempt upload to R2
                if file_stream is not None:
                    upload_ok = await knowledge_storage_service.provider.upload_streaming(
                        file_stream, storage_path, content_type
                    )
                else:
                    upload_ok = await knowledge_storage_service.provider.upload(
                        file_data, storage_path, content_type
                    )
                if not upload_ok:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to upload document version to storage."
                    )

                # Verify Object Integrity / Check (Task 15)
                exists = await knowledge_storage_service.provider.exists(storage_path)
                if not exists:
                    # Rollback storage
                    await knowledge_storage_service.provider.delete(storage_path)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed storage integrity verification check."
                    )

                try:
                    # Database Registration Updates
                    parent_version = existing_doc.version
                    existing_doc.version = new_version_num
                    existing_doc.storage_path = storage_path
                    existing_doc.size = file_size
                    existing_doc.mime_type = content_type
                    existing_doc.upload_status = "uploaded"
                    existing_doc.validation_status = "validated"
                    existing_doc.processing_status = "pending"

                    version_history = KnowledgeVersionHistory(
                        document_id=existing_doc.id,
                        version_number=new_version_num,
                        parent_version_number=parent_version,
                        storage_path=storage_path,
                        reason=f"New version uploaded: {filename}",
                        created_by=user_id
                    )

                    queue_job = KnowledgeProcessingQueue(
                        document_id=existing_doc.id,
                        status="pending"
                    )

                    db.add(existing_doc)
                    db.add(version_history)
                    db.add(queue_job)
                    await db.commit()
                    await db.refresh(existing_doc)

                    # Log activity
                    from app.services.knowledge_collection import knowledge_collection_service
                    await knowledge_collection_service.log_activity(
                        db,
                        collection_id=collection_id,
                        document_id=existing_doc.id,
                        user_id=user_id,
                        activity_type="upload",
                        details={"filename": filename, "version": new_version_num, "status": "version_created"}
                    )

                    from app.core.metrics import knowledge_uploads_total
                    knowledge_uploads_total.labels(collection_id=str(collection_id), file_type=content_type).inc()

                    return {
                        "status": "success",
                        "message": "Document version created successfully.",
                        "document_id": existing_doc.id,
                        "version": new_version_num
                    }
                except Exception as e:
                    await db.rollback()
                    # Clean up uploaded storage file
                    await knowledge_storage_service.provider.delete(storage_path)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Database update failed, changes rolled back. Error: {str(e)}"
                    )

        # If not a duplicate: upload document as a new entity (version 1)
        doc_id = uuid.uuid4()
        ext = os.path.splitext(filename.lower())[1]
        storage_path = knowledge_storage_service.generate_document_path(
            collection_id, doc_id, filename, version=1
        )

        # Attempt upload to R2
        if file_stream is not None:
            upload_ok = await knowledge_storage_service.provider.upload_streaming(
                file_stream, storage_path, content_type
            )
        else:
            upload_ok = await knowledge_storage_service.provider.upload(
                file_data, storage_path, content_type
            )
        if not upload_ok:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload document file to storage."
            )

        # Task 15 Verification
        exists = await knowledge_storage_service.provider.exists(storage_path)
        if not exists:
            await knowledge_storage_service.provider.delete(storage_path)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed storage integrity verification check."
            )

        try:
            db_doc = KnowledgeDocument(
                id=doc_id,
                collection_id=collection_id,
                file_name=filename,
                original_file_name=filename,
                mime_type=content_type,
                extension=ext,
                checksum=checksum,
                storage_path=storage_path,
                version=1,
                size=file_size,
                upload_status="uploaded",
                validation_status="validated",
                processing_status="pending",
                created_by=user_id
            )

            version_history = KnowledgeVersionHistory(
                document_id=doc_id,
                version_number=1,
                parent_version_number=None,
                storage_path=storage_path,
                reason="Initial upload",
                created_by=user_id
            )

            queue_job = KnowledgeProcessingQueue(
                document_id=doc_id,
                status="pending"
            )

            db.add(db_doc)
            db.add(version_history)
            db.add(queue_job)
            await db.commit()
            await db.refresh(db_doc)

            # Log activity
            from app.services.knowledge_collection import knowledge_collection_service
            await knowledge_collection_service.log_activity(
                db,
                collection_id=collection_id,
                document_id=doc_id,
                user_id=user_id,
                activity_type="upload",
                details={"filename": filename, "version": 1, "status": "created"}
            )

            from app.core.metrics import knowledge_uploads_total
            knowledge_uploads_total.labels(collection_id=str(collection_id), file_type=content_type).inc()

            return {
                "status": "success",
                "message": "Document uploaded and registered successfully.",
                "document_id": doc_id,
                "version": 1
            }
        except Exception as e:
            await db.rollback()
            await knowledge_storage_service.provider.delete(storage_path)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database registration failed, changes rolled back. Error: {str(e)}"
            )

    async def bulk_upload_documents(
        self,
        db: AsyncSession,
        collection_id: uuid.UUID,
        files: List[Tuple[str, bytes, str]],  # List of (filename, file_bytes, content_type)
        user_id: uuid.UUID,
        duplicate_strategy: str = "skip"
    ) -> Dict[str, Any]:
        """
        Task 10: Bulk Upload supports partial successes.
        """
        results = []
        success_count = 0
        fail_count = 0

        for filename, data, content_type in files:
            try:
                res = await self.upload_document(
                    db=db,
                    collection_id=collection_id,
                    filename=filename,
                    file_data=data,
                    content_type=content_type,
                    user_id=user_id,
                    duplicate_strategy=duplicate_strategy
                )
                results.append({"filename": filename, "result": res})
                if res.get("status") == "success":
                    success_count += 1
            except Exception as e:
                fail_count += 1
                results.append({
                    "filename": filename,
                    "result": {
                        "status": "failed",
                        "message": str(getattr(e, "detail", str(e)))
                    }
                })

        # Final commit for the entire batch
        await db.commit()

        return {
            "total_files": len(files),
            "success_count": success_count,
            "failure_count": fail_count,
            "details": results
        }

    async def archive_document(
        self, db: AsyncSession, document_id: uuid.UUID, user_id: uuid.UUID, reason: Optional[str] = None
    ) -> KnowledgeDocument:
        """
        Task 8: Archive document (soft-delete record, keep R2 storage file untouched).
        """
        doc = await db.get(KnowledgeDocument, document_id)
        if not doc or doc.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found."
            )

        doc.deleted_at = datetime.now(timezone.utc)
        db.add(doc)
        await db.commit()
        await db.refresh(doc)

        # Log activity
        from app.services.knowledge_collection import knowledge_collection_service
        await knowledge_collection_service.log_activity(
            db,
            collection_id=doc.collection_id,
            document_id=doc.id,
            user_id=user_id,
            activity_type="delete",
            details={"action": "archived", "reason": reason}
        )
        return doc

    async def restore_document(self, db: AsyncSession, document_id: uuid.UUID, user_id: uuid.UUID) -> KnowledgeDocument:
        """
        Restore a soft-deleted document
        """
        result = await db.execute(
            select(KnowledgeDocument).filter(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.deleted_at.is_not(None)
            )
        )
        doc = result.scalars().first()
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found or not in archived state."
            )

        doc.deleted_at = None
        db.add(doc)
        await db.commit()
        await db.refresh(doc)

        # Log activity
        from app.services.knowledge_collection import knowledge_collection_service
        await knowledge_collection_service.log_activity(
            db,
            collection_id=doc.collection_id,
            document_id=doc.id,
            user_id=user_id,
            activity_type="collection_change",
            details={"action": "restored_document"}
        )
        return doc

    async def move_document(
        self, db: AsyncSession, document_id: uuid.UUID, target_collection_id: uuid.UUID, user_id: uuid.UUID
    ) -> KnowledgeDocument:
        """
        Task 9: Move document between Collections.
        """
        doc = await db.get(KnowledgeDocument, document_id)
        if not doc or doc.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found."
            )

        # Verify target collection exists
        target_coll = await db.get(KnowledgeCollection, target_collection_id)
        if not target_coll or target_coll.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target Collection not found."
            )

        old_collection_id = doc.collection_id
        doc.collection_id = target_collection_id
        db.add(doc)
        await db.commit()
        await db.refresh(doc)

        # Log activity
        from app.services.knowledge_collection import knowledge_collection_service
        await knowledge_collection_service.log_activity(
            db,
            collection_id=target_collection_id,
            document_id=doc.id,
            user_id=user_id,
            activity_type="move",
            details={"old_collection_id": str(old_collection_id), "new_collection_id": str(target_collection_id)}
        )
        return doc

    async def list_documents_by_collection(self, db: AsyncSession, collection_id: uuid.UUID) -> List[KnowledgeDocument]:
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(KnowledgeDocument).options(
                selectinload(KnowledgeDocument.tags),
                selectinload(KnowledgeDocument.sources)
            ).filter(
                KnowledgeDocument.collection_id == collection_id,
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        return list(result.scalars().all())

    async def get_document(self, db: AsyncSession, document_id: uuid.UUID) -> Optional[KnowledgeDocument]:
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(KnowledgeDocument).options(
                selectinload(KnowledgeDocument.tags),
                selectinload(KnowledgeDocument.sources)
            ).filter(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        return result.scalars().first()

    async def get_document_version_history(self, db: AsyncSession, document_id: uuid.UUID) -> List[KnowledgeVersionHistory]:
        result = await db.execute(
            select(KnowledgeVersionHistory).filter(
                KnowledgeVersionHistory.document_id == document_id
            ).order_by(KnowledgeVersionHistory.version_number.desc())
        )
        return list(result.scalars().all())

    async def get_processing_jobs(self, db: AsyncSession, document_id: uuid.UUID) -> List[KnowledgeProcessingQueue]:
        result = await db.execute(
            select(KnowledgeProcessingQueue).filter(
                KnowledgeProcessingQueue.document_id == document_id
            ).order_by(KnowledgeProcessingQueue.created_at.desc())
        )
        return list(result.scalars().all())

knowledge_document_service = KnowledgeDocumentService()
