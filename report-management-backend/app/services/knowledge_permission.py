import uuid
from typing import List, Optional, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from fastapi import HTTPException, status

from app.models.knowledge import CollectionPermission, KnowledgeCollection
from app.services.knowledge_cache import knowledge_cache_service

PERMISSION_LEVELS = {
    "owner": 100,
    "administrator": 80,
    "editor": 60,
    "contributor": 40,
    "reviewer": 20,
    "viewer": 10
}

class KnowledgePermissionService:
    async def check_permission(
        self, db: AsyncSession, collection_id: uuid.UUID, user_id: uuid.UUID, required_level: str
    ) -> bool:
        cache_key = f"permission:{collection_id}:{user_id}:{required_level}"
        cached = await knowledge_cache_service.get(cache_key)
        if cached is not None:
            return cached

        # Fetch collection to check owner and organization isolation
        col_res = await db.execute(
            select(KnowledgeCollection).filter(
                KnowledgeCollection.id == collection_id,
                KnowledgeCollection.deleted_at.is_(None)
            )
        )
        col = col_res.scalars().first()
        if not col:
            return False

        # If user is collection owner, they have full access
        if col.owner_id == user_id:
            await knowledge_cache_service.set(cache_key, True)
            return True

        # Check explicit permission
        perm_res = await db.execute(
            select(CollectionPermission).filter(
                CollectionPermission.collection_id == collection_id,
                CollectionPermission.user_id == user_id
            )
        )
        perm = perm_res.scalars().first()
        if not perm:
            # Check if visibility allows public/shared access if required_level is viewer
            if required_level == "viewer" and col.visibility in ("shared", "public"):
                await knowledge_cache_service.set(cache_key, True)
                return True
            await knowledge_cache_service.set(cache_key, False)
            return False

        user_value = PERMISSION_LEVELS.get(perm.permission_level.lower(), 0)
        req_value = PERMISSION_LEVELS.get(required_level.lower(), 0)
        
        has_perm = user_value >= req_value
        await knowledge_cache_service.set(cache_key, has_perm)
        return has_perm

    async def batch_check_permissions(
        self, db: AsyncSession, collection_ids: List[uuid.UUID], user_id: uuid.UUID, required_level: str
    ) -> Set[uuid.UUID]:
        if not collection_ids:
            return set()

        allowed = set()
        missing_ids = []
        for cid in collection_ids:
            cache_key = f"permission:{cid}:{user_id}:{required_level}"
            cached = await knowledge_cache_service.get(cache_key)
            if cached is True:
                allowed.add(cid)
            elif cached is False:
                pass
            else:
                missing_ids.append(cid)

        if not missing_ids:
            return allowed

        col_res = await db.execute(
            select(KnowledgeCollection).filter(
                KnowledgeCollection.id.in_(missing_ids),
                KnowledgeCollection.deleted_at.is_(None)
            )
        )
        collections = col_res.scalars().all()
        col_map = {c.id: c for c in collections}

        perm_res = await db.execute(
            select(CollectionPermission).filter(
                CollectionPermission.collection_id.in_(missing_ids),
                CollectionPermission.user_id == user_id
            )
        )
        permissions = perm_res.scalars().all()
        perm_map = {p.collection_id: p for p in permissions}

        req_value = PERMISSION_LEVELS.get(required_level.lower(), 0)

        for cid in missing_ids:
            cache_key = f"permission:{cid}:{user_id}:{required_level}"
            col = col_map.get(cid)
            if not col:
                await knowledge_cache_service.set(cache_key, False)
                continue

            if col.owner_id == user_id:
                await knowledge_cache_service.set(cache_key, True)
                allowed.add(cid)
                continue

            perm = perm_map.get(cid)
            if not perm:
                if required_level == "viewer" and col.visibility in ("shared", "public"):
                    await knowledge_cache_service.set(cache_key, True)
                    allowed.add(cid)
                else:
                    await knowledge_cache_service.set(cache_key, False)
                continue

            user_value = PERMISSION_LEVELS.get(perm.permission_level.lower(), 0)
            has_perm = user_value >= req_value
            await knowledge_cache_service.set(cache_key, has_perm)
            if has_perm:
                allowed.add(cid)

        return allowed

    async def assign_permission(
        self, db: AsyncSession, collection_id: uuid.UUID, user_id: uuid.UUID, permission_level: str, assigner_id: uuid.UUID
    ) -> CollectionPermission:
        # Check if assigner is owner or administrator
        if not await self.check_permission(db, collection_id, assigner_id, "administrator"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to modify collection permissions."
            )

        # Check if permission already exists
        perm_res = await db.execute(
            select(CollectionPermission).filter(
                CollectionPermission.collection_id == collection_id,
                CollectionPermission.user_id == user_id
            )
        )
        perm = perm_res.scalars().first()
        
        if perm:
            perm.permission_level = permission_level
        else:
            perm = CollectionPermission(
                collection_id=collection_id,
                user_id=user_id,
                permission_level=permission_level
            )
            db.add(perm)
            
        await db.commit()
        await db.refresh(perm)
        
        # Invalidate permission caches
        await knowledge_cache_service.invalidate_collection(collection_id)
        return perm

    async def remove_permission(
        self, db: AsyncSession, collection_id: uuid.UUID, user_id: uuid.UUID, assigner_id: uuid.UUID
    ) -> bool:
        if not await self.check_permission(db, collection_id, assigner_id, "administrator"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to modify collection permissions."
            )
            
        await db.execute(
            delete(CollectionPermission).where(
                CollectionPermission.collection_id == collection_id,
                CollectionPermission.user_id == user_id
            )
        )
        await db.commit()
        await knowledge_cache_service.invalidate_collection(collection_id)
        return True

    async def list_permissions(
        self, db: AsyncSession, collection_id: uuid.UUID, user_id: uuid.UUID
    ) -> List[CollectionPermission]:
        if not await self.check_permission(db, collection_id, user_id, "viewer"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied."
            )
        res = await db.execute(
            select(CollectionPermission).filter(CollectionPermission.collection_id == collection_id)
        )
        return list(res.scalars().all())

knowledge_permission_service = KnowledgePermissionService()
