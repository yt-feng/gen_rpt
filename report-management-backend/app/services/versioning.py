import uuid
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.document import DocumentVersion, DocumentSection, DocumentBlock
from app.services.canonical import VersionManager
from app.services.rendering import rendering_pipeline
from app.services.snapshot import snapshot_engine
from app.models.enums import DocChangeType
from app.logging.logger import logger

class VersioningService:
    
    @staticmethod
    async def compare_versions(db: AsyncSession, version_a_id: uuid.UUID, version_b_id: uuid.UUID) -> Dict[str, Any]:
        """
        Compares two versions structurally based on stable_id.
        Returns a diff indicating nodes added, removed, or modified.
        """
        tree_a = await rendering_pipeline.get_version_tree(db, version_a_id)
        tree_b = await rendering_pipeline.get_version_tree(db, version_b_id)
        
        # Flatten blocks into dictionaries keyed by stable_id
        blocks_a = {b.stable_id: b for sec in tree_a for b in sec.blocks}
        blocks_b = {b.stable_id: b for sec in tree_b for b in sec.blocks}
        
        diff = {
            "added": [],
            "removed": [],
            "modified": [],
            "unchanged": []
        }
        
        all_ids = set(blocks_a.keys()).union(set(blocks_b.keys()))
        
        for bid in all_ids:
            if bid not in blocks_a:
                diff["added"].append(bid)
            elif bid not in blocks_b:
                diff["removed"].append(bid)
            else:
                # Compare markdown/content
                b1 = blocks_a[bid]
                b2 = blocks_b[bid]
                if b1.markdown != b2.markdown or b1.content_json != b2.content_json:
                    diff["modified"].append({
                        "stable_id": bid,
                        "old_markdown": b1.markdown,
                        "new_markdown": b2.markdown
                    })
                else:
                    diff["unchanged"].append(bid)
                    
        return diff

    @staticmethod
    async def restore_version(
        db: AsyncSession, 
        document_id: uuid.UUID, 
        current_version_id: uuid.UUID, 
        target_version_id: uuid.UUID, 
        actor_id: uuid.UUID
    ) -> DocumentVersion:
        """
        Restores a past version by creating a NEW version that is an exact clone of the target.
        """
        logger.info(f"Restoring document {document_id} to version {target_version_id}")
        
        new_version = await VersionManager.create_new_version(
            db=db,
            document_id=document_id,
            parent_version_id=current_version_id,
            change_type=DocChangeType.RESTORE,
            actor_id=actor_id,
            summary=f"Restored from version {target_version_id}"
        )
        
        # Wait, create_new_version clones the parent. 
        # But we want to clone the TARGET version's blocks, NOT the parent's!
        # So we actually need to wipe the newly cloned blocks and copy the target blocks instead.
        # Alternatively, we just use target_version_id as the "parent" for cloning purposes.
        
        # Delete the automatically cloned sections/blocks
        stmt_del_blocks = DocumentBlock.__table__.delete().where(
            DocumentBlock.section_id.in_(
                select(DocumentSection.id).where(DocumentSection.version_id == new_version.id)
            )
        )
        await db.execute(stmt_del_blocks)
        
        stmt_del_secs = DocumentSection.__table__.delete().where(DocumentSection.version_id == new_version.id)
        await db.execute(stmt_del_secs)
        
        # Now deep copy the TARGET version
        target_tree = await rendering_pipeline.get_version_tree(db, target_version_id)
        for t_sec in target_tree:
            new_sec = DocumentSection(
                id=uuid.uuid4(),
                version_id=new_version.id,
                stable_id=t_sec.stable_id,
                section_order=t_sec.section_order,
                title=t_sec.title,
                section_type=t_sec.section_type
            )
            db.add(new_sec)
            await db.flush()
            
            for t_block in t_sec.blocks:
                new_block = DocumentBlock(
                    id=uuid.uuid4(),
                    section_id=new_sec.id,
                    stable_id=t_block.stable_id,
                    block_order=t_block.block_order,
                    block_type=t_block.block_type,
                    content_json=t_block.content_json.copy() if t_block.content_json else None,
                    markdown=t_block.markdown,
                    html=t_block.html,
                    metadata_=t_block.metadata_.copy() if t_block.metadata_ else {}
                )
                db.add(new_block)
        
        await db.flush()
        
        # Generate new snapshot
        await snapshot_engine.generate_snapshot(db, document_id, new_version.id)
        return new_version

    @staticmethod
    async def rollback_node(
        db: AsyncSession,
        document_id: uuid.UUID,
        current_version_id: uuid.UUID,
        target_version_id: uuid.UUID,
        node_stable_id: str,
        actor_id: uuid.UUID
    ) -> DocumentVersion:
        """
        Rolls back a single node to its state in a target version, creating a new DocumentVersion.
        """
        logger.info(f"Rolling back node {node_stable_id} to state in version {target_version_id}")
        
        # Find the target block state
        stmt = select(DocumentBlock).join(DocumentSection).where(
            DocumentSection.version_id == target_version_id,
            DocumentBlock.stable_id == node_stable_id
        )
        target_block_state = (await db.execute(stmt)).scalars().first()
        
        if not target_block_state:
            raise ValueError("Target node state not found in the specified version.")
            
        # 1. Create new version clone of CURRENT version
        new_version = await VersionManager.create_new_version(
            db=db,
            document_id=document_id,
            parent_version_id=current_version_id,
            change_type=DocChangeType.ROLLBACK,
            actor_id=actor_id,
            summary=f"Rolled back node {node_stable_id} to version {target_version_id}"
        )
        
        # 2. Overwrite the specific block with target state
        stmt_update = update(DocumentBlock).where(
            DocumentBlock.section_id.in_(
                select(DocumentSection.id).where(DocumentSection.version_id == new_version.id)
            ),
            DocumentBlock.stable_id == node_stable_id
        ).values(
            markdown=target_block_state.markdown,
            content_json=target_block_state.content_json
        )
        await db.execute(stmt_update)
        
        # 3. Snapshot
        await snapshot_engine.generate_snapshot(db, document_id, new_version.id)
        return new_version

versioning_service = VersioningService()
