import uuid
from typing import List, Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status

from app.models.knowledge import KnowledgeCategory
from app.schemas.knowledge import CategoryCreate, CategoryUpdate, CategoryTreeResponse
from app.services.knowledge_cache import knowledge_cache_service

class KnowledgeCategoryService:
    async def create_category(self, db: AsyncSession, obj_in: CategoryCreate) -> KnowledgeCategory:
        # Check uniqueness of slug
        existing_res = await db.execute(
            select(KnowledgeCategory).filter(KnowledgeCategory.slug == obj_in.slug)
        )
        if existing_res.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Category with slug '{obj_in.slug}' already exists."
            )
            
        category = KnowledgeCategory(
            name=obj_in.name,
            slug=obj_in.slug,
            parent_id=obj_in.parent_id,
            display_order=obj_in.display_order,
            status=obj_in.status,
            description=obj_in.description
        )
        db.add(category)
        await db.commit()
        await db.refresh(category)
        knowledge_cache_service.invalidate_categories()
        return category

    async def get_category(self, db: AsyncSession, category_id: uuid.UUID) -> Optional[KnowledgeCategory]:
        return await db.get(KnowledgeCategory, category_id)

    async def update_category(self, db: AsyncSession, category_id: uuid.UUID, obj_in: CategoryUpdate) -> KnowledgeCategory:
        category = await self.get_category(db, category_id)
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Category not found."
            )
            
        update_data = obj_in.model_dump(exclude_unset=True)
        for field in update_data:
            setattr(category, field, update_data[field])
            
        db.add(category)
        await db.commit()
        await db.refresh(category)
        knowledge_cache_service.invalidate_categories()
        return category

    async def delete_category(self, db: AsyncSession, category_id: uuid.UUID) -> bool:
        category = await self.get_category(db, category_id)
        if not category:
            return False
            
        # Re-parent children before delete to avoid dangling nodes
        await db.execute(
            KnowledgeCategory.__table__.update()
            .where(KnowledgeCategory.parent_id == category_id)
            .values(parent_id=category.parent_id)
        )
        await db.delete(category)
        await db.commit()
        knowledge_cache_service.invalidate_categories()
        return True

    async def get_category_tree(self, db: AsyncSession) -> List[CategoryTreeResponse]:
        cache_key = "categories:tree"
        cached = knowledge_cache_service.get(cache_key)
        if cached is not None:
            return cached
            
        res = await db.execute(
            select(KnowledgeCategory).order_by(
                KnowledgeCategory.parent_id.asc(),
                KnowledgeCategory.display_order.asc()
            )
        )
        all_categories = res.scalars().all()
        
        # Build node tree
        category_map = {}
        roots = []
        
        for cat in all_categories:
            node = CategoryTreeResponse(
                id=cat.id,
                name=cat.name,
                slug=cat.slug,
                parent_id=cat.parent_id,
                display_order=cat.display_order,
                status=cat.status,
                description=cat.description,
                children=[]
            )
            category_map[cat.id] = node
            
        for node in category_map.values():
            if node.parent_id and node.parent_id in category_map:
                category_map[node.parent_id].children.append(node)
            else:
                roots.append(node)
                
        knowledge_cache_service.set(cache_key, roots)
        return roots

knowledge_category_service = KnowledgeCategoryService()
