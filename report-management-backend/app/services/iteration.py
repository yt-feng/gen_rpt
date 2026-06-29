import uuid
import time
from typing import Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from fastapi import HTTPException

from app.models.document import DocumentVersion, DocumentSection, DocumentBlock
from app.models.iteration import IterationHistory
from app.services.canonical import VersionManager
from app.services.rendering import RenderingPipeline
from app.logging.logger import logger

class IterationEngine:
    @staticmethod
    async def get_node_context(db: AsyncSession, version_id: uuid.UUID, stable_id: str) -> Dict:
        """
        Retrieves surrounding context (previous block, current block, next block)
        for context-aware AI regeneration.
        """
        # Find the target block
        stmt = select(DocumentBlock).join(DocumentSection).where(
            DocumentSection.version_id == version_id,
            DocumentBlock.stable_id == stable_id
        )
        target = (await db.execute(stmt)).scalars().first()
        if not target:
            raise HTTPException(status_code=404, detail="Block node not found in this version.")

        # In a full implementation, we would fetch the surrounding blocks based on section_order/block_order
        return {
            "current_content": target.markdown or target.content_json,
            "target_block": target
        }

    @staticmethod
    async def human_edit_node(
        db: AsyncSession,
        document_id: uuid.UUID,
        parent_version_id: uuid.UUID,
        stable_id: str,
        new_markdown: str,
        actor_id: uuid.UUID
    ) -> DocumentVersion:
        """
        Performs a human edit on a single node, returning a new Version snapshot.
        """
        async with db.begin():
            context = await IterationEngine.get_node_context(db, parent_version_id, stable_id)
            old_content = context["current_content"]

            # 1. Create new version structure (deep copy of sections/blocks)
            new_version = await VersionManager.create_new_version(
                db=db,
                document_id=document_id,
                parent_version_id=parent_version_id,
                change_type="HUMAN_EDIT",
                actor_id=actor_id,
                summary=f"Human edit on node {stable_id}"
            )

            # 2. Update the specific block in the new version
            stmt = update(DocumentBlock).where(
                DocumentBlock.section_id.in_(
                    select(DocumentSection.id).where(DocumentSection.version_id == new_version.id)
                ),
                DocumentBlock.stable_id == stable_id
            ).values(markdown=new_markdown, content_json={"text": new_markdown})
            
            await db.execute(stmt)

            # 3. Log Iteration History
            history = IterationHistory(
                document_id=document_id,
                version_id=new_version.id,
                stable_id=stable_id,
                actor_type="Human",
                reviewer_id=actor_id,
                previous_content={"markdown": old_content},
                new_content={"markdown": new_markdown}
            )
            db.add(history)

            # 4. Trigger Rendering Pipeline (Synchronize HTML/Markdown/PDF)
            await RenderingPipeline.execute_pipeline(db, new_version.id)
            
            logger.info(f"Human edited node {stable_id}, created version {new_version.version_number}")
            return new_version

    @staticmethod
    async def regenerate_node(
        db: AsyncSession,
        document_id: uuid.UUID,
        parent_version_id: uuid.UUID,
        stable_id: str,
        instruction: str,
        actor_id: uuid.UUID
    ) -> DocumentVersion:
        """
        Context-aware AI block-level regeneration.
        """
        async with db.begin():
            start_time = time.time()
            context = await IterationEngine.get_node_context(db, parent_version_id, stable_id)
            old_content = context["current_content"]

            # Mocking the AI call for Phase 6 API foundation
            # In reality, we would pass `context` and `instruction` to DeepSeek/Groq here
            generated_markdown = f"{old_content}\n\n*[AI Refined based on: {instruction}]*"

            # 1. Create new version
            new_version = await VersionManager.create_new_version(
                db=db,
                document_id=document_id,
                parent_version_id=parent_version_id,
                change_type="AI_REGENERATION",
                actor_id=actor_id,
                summary=f"AI regeneration of node {stable_id}"
            )

            # 2. Update the block
            stmt = update(DocumentBlock).where(
                DocumentBlock.section_id.in_(
                    select(DocumentSection.id).where(DocumentSection.version_id == new_version.id)
                ),
                DocumentBlock.stable_id == stable_id
            ).values(markdown=generated_markdown, content_json={"text": generated_markdown})
            await db.execute(stmt)

            # 3. Log AI Iteration History
            exec_time = time.time() - start_time
            history = IterationHistory(
                document_id=document_id,
                version_id=new_version.id,
                stable_id=stable_id,
                actor_type="AI",
                reviewer_id=actor_id,
                previous_content={"markdown": old_content},
                new_content={"markdown": generated_markdown},
                prompt=instruction,
                context_used={"previous_block": "...", "next_block": "..."},
                model="deepseek-chat",
                execution_time=exec_time
            )
            db.add(history)

            # 4. Trigger Rendering Pipeline
            await RenderingPipeline.execute_pipeline(db, new_version.id)
            
            logger.info(f"AI regenerated node {stable_id}, created version {new_version.version_number}")
            return new_version

iteration_engine = IterationEngine()
