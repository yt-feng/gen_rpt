import uuid
import re
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.document import DocumentVersion, DocumentSection, DocumentBlock
from app.models.enums import SectionContentType, BlockContentType

class VersionManager:
    @staticmethod
    async def create_new_version(
        db: AsyncSession,
        document_id: uuid.UUID,
        parent_version_id: uuid.UUID,
        change_type: str,
        actor_id: Optional[uuid.UUID] = None,
        summary: Optional[str] = None
    ) -> DocumentVersion:
        """
        Creates a new DocumentVersion.
        It strictly duplicates the structure (Sections, Blocks) of the parent_version
        so they can be mutated individually without affecting the parent snapshot.
        """
        # Get parent version to determine new version_number
        stmt = select(DocumentVersion).where(
            DocumentVersion.document_id == document_id
        ).order_by(DocumentVersion.version_number.desc())
        
        result = await db.execute(stmt)
        latest_version = result.scalars().first()
        
        new_version_number = 1
        if latest_version:
            new_version_number = latest_version.version_number + 1

        new_version = DocumentVersion(
            document_id=document_id,
            version_number=new_version_number,
            parent_version=parent_version_id,
            change_type=change_type,
            created_by=actor_id,
            summary=summary,
            status="draft" # Or inherit from parent
        )
        db.add(new_version)
        await db.flush() # flush to get new_version.id

        # Copy sections and blocks
        stmt_sections = select(DocumentSection).where(DocumentSection.version_id == parent_version_id)
        sections_result = await db.execute(stmt_sections)
        parent_sections = sections_result.scalars().all()

        for p_sec in parent_sections:
            new_sec = DocumentSection(
                version_id=new_version.id,
                stable_id=p_sec.stable_id,
                section_order=p_sec.section_order,
                title=p_sec.title,
                section_type=p_sec.section_type
            )
            db.add(new_sec)
            await db.flush()

            stmt_blocks = select(DocumentBlock).where(DocumentBlock.section_id == p_sec.id)
            blocks_result = await db.execute(stmt_blocks)
            parent_blocks = blocks_result.scalars().all()

            for p_block in parent_blocks:
                new_block = DocumentBlock(
                    section_id=new_sec.id,
                    stable_id=p_block.stable_id,
                    block_order=p_block.block_order,
                    block_type=p_block.block_type,
                    content_json=p_block.content_json.copy() if p_block.content_json else None,
                    markdown=p_block.markdown,
                    html=p_block.html,
                    metadata_=p_block.metadata_.copy() if p_block.metadata_ else {}
                )
                db.add(new_block)

        return new_version

class MarkdownParser:
    @staticmethod
    def parse_to_canonical(markdown_text: str, version_id: uuid.UUID) -> List[DocumentSection]:
        """
        A heuristic parser to convert raw Markdown into Canonical Sections and Blocks.
        This provides backward compatibility with V1 Github Actions.
        """
        # Very simple regex-based chunker for prototype
        sections = []
        lines = markdown_text.split('\n')
        
        current_section = None
        current_blocks = []
        section_order = 1
        block_order = 1
        
        buffer = []
        
        def flush_buffer():
            nonlocal block_order, current_blocks, buffer
            if not buffer:
                return
            
            content = '\n'.join(buffer).strip()
            if not content:
                buffer = []
                return
                
            block_type = BlockContentType.paragraph
            if content.startswith('|') and '|' in content:
                block_type = BlockContentType.table
            elif content.startswith('- ') or content.startswith('* '):
                block_type = BlockContentType.list
            elif content.startswith('>'):
                block_type = BlockContentType.quote
            elif content.startswith('```'):
                block_type = BlockContentType.code
            elif content.startswith('![') or '<img' in content:
                block_type = BlockContentType.image
            
            # Simple content representation
            block = DocumentBlock(
                stable_id=f"block_{uuid.uuid4().hex[:8]}",
                block_order=block_order,
                block_type=block_type,
                markdown=content,
                content_json={"text": content}
            )
            current_blocks.append(block)
            block_order += 1
            buffer = []

        for line in lines:
            if line.startswith('#'):
                flush_buffer()
                
                # Close previous section
                if current_section:
                    current_section.blocks = current_blocks
                    sections.append(current_section)
                
                # Start new section
                title = line.lstrip('#').strip()
                current_section = DocumentSection(
                    version_id=version_id,
                    stable_id=f"sec_{uuid.uuid4().hex[:8]}",
                    section_order=section_order,
                    title=title,
                    section_type=SectionContentType.Custom
                )
                current_blocks = []
                section_order += 1
                block_order = 1
            else:
                if not current_section:
                    # Create a default generic section if none exists
                    current_section = DocumentSection(
                        version_id=version_id,
                        stable_id=f"sec_intro_{uuid.uuid4().hex[:8]}",
                        section_order=section_order,
                        title="Introduction",
                        section_type=SectionContentType.Introduction
                    )
                    section_order += 1
                buffer.append(line)
                
                # Basic heuristic: empty lines delineate blocks
                if not line.strip() and buffer:
                    flush_buffer()
                    
        flush_buffer()
        if current_section:
            current_section.blocks = current_blocks
            sections.append(current_section)
            
        return sections

canonical_service = VersionManager()
