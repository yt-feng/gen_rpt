import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from app.models.knowledge import KnowledgeCollection, KnowledgeDocument, KnowledgeActivityHistory, KnowledgeProcessingQueue
from app.schemas.knowledge import CollectionCreate, CollectionUpdate
from app.repositories.knowledge import collection_repo

class KnowledgeCollectionService:
    async def create_collection(
        self, db: AsyncSession, obj_in: CollectionCreate, user_id: uuid.UUID
    ) -> KnowledgeCollection:
        # Check if name or slug already exists
        existing_name = await db.execute(
            select(KnowledgeCollection).filter(
                KnowledgeCollection.name == obj_in.name,
                KnowledgeCollection.deleted_at.is_(None)
            )
        )
        if existing_name.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Collection with name '{obj_in.name}' already exists."
            )

        existing_slug = await db.execute(
            select(KnowledgeCollection).filter(
                KnowledgeCollection.slug == obj_in.slug,
                KnowledgeCollection.deleted_at.is_(None)
            )
        )
        if existing_slug.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Collection with slug '{obj_in.slug}' already exists."
            )

        db_obj = KnowledgeCollection(
            name=obj_in.name,
            slug=obj_in.slug,
            description=obj_in.description,
            status=obj_in.status or "active",
            owner_id=user_id,
            organization_id=obj_in.organization_id,
            visibility=obj_in.visibility or "public",
            created_by=user_id
        )
        db.add(db_obj)
        await db.commit()

        await self.log_activity(
            db,
            collection_id=db_obj.id,
            user_id=user_id,
            activity_type="collection_change",
            details={"action": "created", "name": db_obj.name}
        )
        return await self.get_collection(db, db_obj.id)

    async def update_collection(
        self, db: AsyncSession, collection_id: uuid.UUID, obj_in: CollectionUpdate, user_id: uuid.UUID
    ) -> KnowledgeCollection:
        db_obj = await self.get_collection(db, collection_id)
        if not db_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Collection not found."
            )

        update_data = obj_in.model_dump(exclude_unset=True)

        if "name" in update_data and update_data["name"] != db_obj.name:
            existing_name = await db.execute(
                select(KnowledgeCollection).filter(
                    KnowledgeCollection.name == update_data["name"],
                    KnowledgeCollection.id != collection_id,
                    KnowledgeCollection.deleted_at.is_(None)
                )
            )
            if existing_name.scalars().first():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Collection with name '{update_data['name']}' already exists."
                )

        if "slug" in update_data and update_data["slug"] != db_obj.slug:
            existing_slug = await db.execute(
                select(KnowledgeCollection).filter(
                    KnowledgeCollection.slug == update_data["slug"],
                    KnowledgeCollection.id != collection_id,
                    KnowledgeCollection.deleted_at.is_(None)
                )
            )
            if existing_slug.scalars().first():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Collection with slug '{update_data['slug']}' already exists."
                )

        for field in update_data:
            setattr(db_obj, field, update_data[field])

        db_obj.updated_by = user_id
        db.add(db_obj)
        await db.commit()

        await self.log_activity(
            db,
            collection_id=db_obj.id,
            user_id=user_id,
            activity_type="collection_change",
            details={"action": "updated", "fields": list(update_data.keys())}
        )
        return await self.get_collection(db, db_obj.id)

    async def archive_collection(self, db: AsyncSession, collection_id: uuid.UUID, user_id: uuid.UUID) -> KnowledgeCollection:
        db_obj = await self.get_collection(db, collection_id)
        if not db_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Collection not found."
            )
        db_obj.status = "archived"
        db_obj.updated_by = user_id
        db.add(db_obj)
        await db.commit()

        await self.log_activity(
            db,
            collection_id=db_obj.id,
            user_id=user_id,
            activity_type="collection_change",
            details={"action": "archived"}
        )
        return await self.get_collection(db, db_obj.id)

    async def restore_collection(self, db: AsyncSession, collection_id: uuid.UUID, user_id: uuid.UUID) -> KnowledgeCollection:
        db_obj = await self.get_collection(db, collection_id)
        if not db_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Collection not found."
            )
        db_obj.status = "active"
        db_obj.updated_by = user_id
        db.add(db_obj)
        await db.commit()

        await self.log_activity(
            db,
            collection_id=db_obj.id,
            user_id=user_id,
            activity_type="collection_change",
            details={"action": "restored"}
        )
        return await self.get_collection(db, db_obj.id)

    async def delete_collection(self, db: AsyncSession, collection_id: uuid.UUID, user_id: uuid.UUID) -> KnowledgeCollection:
        db_obj = await self.get_collection(db, collection_id)
        if not db_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Collection not found."
            )
        db_obj.deleted_at = datetime.now(timezone.utc)
        db_obj.updated_by = user_id
        db.add(db_obj)
        await db.commit()

        await self.log_activity(
            db,
            collection_id=db_obj.id,
            user_id=user_id,
            activity_type="collection_change",
            details={"action": "soft_deleted"}
        )
        return await self.get_collection_with_deleted(db, collection_id)

    async def get_collection_with_deleted(self, db: AsyncSession, collection_id: uuid.UUID) -> Optional[KnowledgeCollection]:
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(KnowledgeCollection).options(
                selectinload(KnowledgeCollection.tags)
            ).filter(
                KnowledgeCollection.id == collection_id
            )
        )
        return result.scalars().first()

    async def get_collection(self, db: AsyncSession, collection_id: uuid.UUID) -> Optional[KnowledgeCollection]:
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(KnowledgeCollection).options(
                selectinload(KnowledgeCollection.tags)
            ).filter(
                KnowledgeCollection.id == collection_id,
                KnowledgeCollection.deleted_at.is_(None)
            )
        )
        return result.scalars().first()

    async def list_collections(self, db: AsyncSession, owner_id: uuid.UUID) -> List[KnowledgeCollection]:
        from sqlalchemy.orm import selectinload
        from sqlalchemy import or_
        result = await db.execute(
            select(KnowledgeCollection).options(
                selectinload(KnowledgeCollection.tags)
            ).filter(
                or_(
                    KnowledgeCollection.owner_id == owner_id,
                    KnowledgeCollection.visibility.in_(["public", "shared"])
                ),
                KnowledgeCollection.deleted_at.is_(None)
            )
        )
        return list(result.scalars().all())

    async def get_collection_stats(self, db: AsyncSession, collection_id: uuid.UUID) -> Dict[str, Any]:
        doc_count_res = await db.execute(
            select(func.count(KnowledgeDocument.id)).filter(
                KnowledgeDocument.collection_id == collection_id,
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        total_docs = doc_count_res.scalar() or 0

        storage_res = await db.execute(
            select(func.sum(KnowledgeDocument.size)).filter(
                KnowledgeDocument.collection_id == collection_id,
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        total_size = storage_res.scalar() or 0

        pending_queue_res = await db.execute(
            select(func.count(KnowledgeProcessingQueue.id)).join(
                KnowledgeDocument, KnowledgeDocument.id == KnowledgeProcessingQueue.document_id
            ).filter(
                KnowledgeDocument.collection_id == collection_id,
                KnowledgeDocument.deleted_at.is_(None),
                KnowledgeProcessingQueue.status == "pending"
            )
        )
        pending_queue_count = pending_queue_res.scalar() or 0

        return {
            "document_count": total_docs,
            "storage_usage_bytes": total_size,
            "pending_processing_jobs": pending_queue_count
        }

    async def log_activity(
        self,
        db: AsyncSession,
        collection_id: Optional[uuid.UUID],
        user_id: Optional[uuid.UUID],
        activity_type: str,
        details: Optional[Dict[str, Any]] = None,
        document_id: Optional[uuid.UUID] = None
    ) -> KnowledgeActivityHistory:
        log_entry = KnowledgeActivityHistory(
            collection_id=collection_id,
            document_id=document_id,
            user_id=user_id,
            activity_type=activity_type,
            details=details
        )
        db.add(log_entry)
        await db.commit()
        return log_entry

    async def clone_collection(
        self, db: AsyncSession, collection_id: uuid.UUID, target_name: str, target_slug: str, user_id: uuid.UUID
    ) -> KnowledgeCollection:
        # Check source collection exists
        src = await self.get_collection(db, collection_id)
        if not src:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source collection not found.")
            
        # Check target slug/name uniqueness
        existing_res = await db.execute(
            select(KnowledgeCollection).filter(
                (KnowledgeCollection.name == target_name) | (KnowledgeCollection.slug == target_slug)
            ).filter(KnowledgeCollection.deleted_at.is_(None))
        )
        if existing_res.scalars().first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Collection name or slug already exists.")
            
        # Create cloned collection
        clone = KnowledgeCollection(
            name=target_name,
            slug=target_slug,
            description=f"Cloned from {src.name}. " + (src.description or ""),
            status="active",
            owner_id=user_id,
            organization_id=src.organization_id,
            visibility=src.visibility,
            created_by=user_id
        )
        db.add(clone)
        await db.commit()
        await db.refresh(clone)
        
        # Clone active documents in the collection
        doc_res = await db.execute(
            select(KnowledgeDocument).filter(
                KnowledgeDocument.collection_id == collection_id,
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        docs = doc_res.scalars().all()
        
        for doc in docs:
            cloned_doc = KnowledgeDocument(
                collection_id=clone.id,
                file_name=doc.file_name,
                original_file_name=doc.original_file_name,
                mime_type=doc.mime_type,
                extension=doc.extension,
                checksum=doc.checksum,
                storage_path=doc.storage_path,
                version=doc.version,
                size=doc.size,
                language=doc.language,
                page_count=doc.page_count,
                processing_status=doc.processing_status,
                upload_status=doc.upload_status,
                validation_status=doc.validation_status,
                created_by=user_id
            )
            db.add(cloned_doc)
            
        await db.commit()
        await self.log_activity(
            db,
            collection_id=clone.id,
            user_id=user_id,
            activity_type="collection_change",
            details={"action": "cloned", "source_collection_id": str(collection_id)}
        )
        return await self.get_collection(db, clone.id)

knowledge_collection_service = KnowledgeCollectionService()
