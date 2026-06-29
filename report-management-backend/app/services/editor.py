import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.models.editor import NodeLock, NodeEditHistory
from app.models.document import Document, DocumentVersion, DocumentSection, DocumentBlock
from app.models.enums import EditorActionType, DocChangeType, ReleaseStatus

from app.services.canonical import VersionManager
from app.services.snapshot import snapshot_engine

class EditorService:
    @staticmethod
    async def acquire_lock(db: AsyncSession, document_id: uuid.UUID, node_stable_id: str, owner_id: uuid.UUID, timeout_minutes: int = 5) -> NodeLock:
        # Check if already locked by someone else
        now = datetime.now(timezone.utc)
        stmt = select(NodeLock).where(
            NodeLock.document_id == document_id, 
            NodeLock.node_stable_id == node_stable_id
        )
        result = await db.execute(stmt)
        existing_lock = result.scalars().first()
        
        if existing_lock:
            if existing_lock.owner_id != owner_id and existing_lock.expires_at > now:
                raise ValueError("Node is currently locked by another user")
            # If owned by same user or expired, overwrite it
            existing_lock.owner_id = owner_id
            existing_lock.locked_at = now
            existing_lock.expires_at = now + timedelta(minutes=timeout_minutes)
            await db.commit()
            await db.refresh(existing_lock)
            return existing_lock
            
        new_lock = NodeLock(
            id=uuid.uuid4(),
            document_id=document_id,
            node_stable_id=node_stable_id,
            owner_id=owner_id,
            locked_at=now,
            expires_at=now + timedelta(minutes=timeout_minutes)
        )
        db.add(new_lock)
        await db.commit()
        await db.refresh(new_lock)
        return new_lock

    @staticmethod
    async def release_lock(db: AsyncSession, document_id: uuid.UUID, node_stable_id: str, owner_id: uuid.UUID) -> bool:
        stmt = delete(NodeLock).where(
            NodeLock.document_id == document_id,
            NodeLock.node_stable_id == node_stable_id,
            NodeLock.owner_id == owner_id
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount > 0

    @staticmethod
    async def start_draft_session(db: AsyncSession, document_id: uuid.UUID, editor_id: uuid.UUID) -> DocumentVersion:
        doc = await db.get(Document, document_id)
        if not doc.current_version_id:
            raise ValueError("No active version found to draft from")
            
        # Check if a draft already exists for this document
        stmt = select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.release_status == ReleaseStatus.Draft,
            DocumentVersion.created_by == editor_id
        ).order_by(DocumentVersion.version_number.desc())
        
        result = await db.execute(stmt)
        existing_draft = result.scalars().first()
        if existing_draft:
            return existing_draft
            
        new_draft = await VersionManager.create_new_version(
            db=db,
            document_id=document_id,
            parent_version_id=doc.current_version_id,
            change_type=DocChangeType.HUMAN_EDIT,
            actor_id=editor_id,
            summary="Editing Draft"
        )
        new_draft.release_status = ReleaseStatus.Draft
        await db.commit()
        return new_draft

    @staticmethod
    async def commit_draft_session(db: AsyncSession, document_id: uuid.UUID, draft_version_id: uuid.UUID, editor_id: uuid.UUID) -> DocumentVersion:
        draft = await db.get(DocumentVersion, draft_version_id)
        if not draft or draft.release_status != ReleaseStatus.Draft:
            raise ValueError("Invalid draft version")
            
        draft.release_status = ReleaseStatus.Internal_Review
        
        doc = await db.get(Document, document_id)
        doc.current_version_id = draft.id
        await db.commit()
        
        # Snapshot Engine dynamically renders HTML/Markdown/PDF for the committed draft
        await snapshot_engine.generate_snapshot(db, doc.id, draft.id)
        
        return draft

    @staticmethod
    async def update_node_content(
        db: AsyncSession, 
        draft_version_id: uuid.UUID, 
        node_stable_id: str, 
        new_payload: Dict[str, Any], 
        editor_id: uuid.UUID,
        reason: Optional[str] = "Manual Edit"
    ) -> NodeEditHistory:
        """
        Autosaves a specific node's content into the draft version.
        Supports both DocumentBlock (content_json, markdown) and DocumentSection (title).
        """
        # Search Block first
        stmt = select(DocumentBlock).join(DocumentSection).where(
            DocumentSection.version_id == draft_version_id,
            DocumentBlock.stable_id == node_stable_id
        )
        result = await db.execute(stmt)
        block = result.scalars().first()
        
        old_val = {}
        if block:
            old_val = {"markdown": block.markdown, "content_json": block.content_json}
            if "markdown" in new_payload:
                block.markdown = new_payload["markdown"]
            if "content_json" in new_payload:
                block.content_json = new_payload["content_json"]
        else:
            # Search Section
            stmt = select(DocumentSection).where(
                DocumentSection.version_id == draft_version_id,
                DocumentSection.stable_id == node_stable_id
            )
            res = await db.execute(stmt)
            section = res.scalars().first()
            if not section:
                raise ValueError("Node not found in this version")
                
            old_val = {"title": section.title}
            if "title" in new_payload:
                section.title = new_payload["title"]

        # Log history
        history = NodeEditHistory(
            id=uuid.uuid4(),
            version_id=draft_version_id,
            node_stable_id=node_stable_id,
            editor_id=editor_id,
            edit_type=EditorActionType.Human,
            old_value=old_val,
            new_value=new_payload,
            reason=reason,
            timestamp=datetime.now(timezone.utc)
        )
        db.add(history)
        await db.commit()
        await db.refresh(history)
        return history

    @staticmethod
    async def ai_node_rewrite(
        db: AsyncSession, 
        draft_version_id: uuid.UUID, 
        node_stable_id: str, 
        prompt: str, 
        editor_id: uuid.UUID
    ) -> NodeEditHistory:
        """
        Applies an AI rewrite action to a node within a draft version.
        """
        stmt = select(DocumentBlock).join(DocumentSection).where(
            DocumentSection.version_id == draft_version_id,
            DocumentBlock.stable_id == node_stable_id
        )
        result = await db.execute(stmt)
        block = result.scalars().first()
        
        if not block:
            raise ValueError("Node must be a block for AI rewrite")
            
        old_val = {"markdown": block.markdown}
        
        # Mocking LLM rewrite
        new_md = f"[AI Rewritten: {prompt}] {block.markdown}"
        block.markdown = new_md
        
        history = NodeEditHistory(
            id=uuid.uuid4(),
            version_id=draft_version_id,
            node_stable_id=node_stable_id,
            editor_id=editor_id,
            edit_type=EditorActionType.AI,
            old_value=old_val,
            new_value={"markdown": new_md},
            reason=f"AI Request: {prompt}",
            timestamp=datetime.now(timezone.utc)
        )
        db.add(history)
        await db.commit()
        await db.refresh(history)
        return history

editor_service = EditorService()
