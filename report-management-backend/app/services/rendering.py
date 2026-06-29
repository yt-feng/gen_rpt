import uuid
from typing import List, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.document import DocumentVersion, DocumentSection, DocumentBlock
from app.models.enums import BlockContentType
from app.logging.logger import logger

class RenderingPipeline:
    @staticmethod
    async def get_version_tree(db: AsyncSession, version_id: uuid.UUID) -> List[DocumentSection]:
        stmt = select(DocumentSection).where(
            DocumentSection.version_id == version_id
        ).order_by(DocumentSection.section_order)
        result = await db.execute(stmt)
        sections = result.scalars().all()
        
        for section in sections:
            stmt_blocks = select(DocumentBlock).where(
                DocumentBlock.section_id == section.id
            ).order_by(DocumentBlock.block_order)
            blocks_res = await db.execute(stmt_blocks)
            section.blocks = blocks_res.scalars().all()
            
        return sections

    @staticmethod
    async def render_markdown(db: AsyncSession, version_id: uuid.UUID) -> str:
        """
        Regenerate Markdown output from the canonical sections and blocks.
        """
        sections = await RenderingPipeline.get_version_tree(db, version_id)
        
        md_lines = []
        for section in sections:
            md_lines.append(f"# {section.title}\n")
            for block in section.blocks:
                if block.markdown:
                    md_lines.append(block.markdown)
                elif block.content_json and "text" in block.content_json:
                    md_lines.append(block.content_json["text"])
                md_lines.append("") # Empty line between blocks
        
        return "\n".join(md_lines)

    @staticmethod
    async def render_html(db: AsyncSession, version_id: uuid.UUID) -> str:
        """
        Regenerate HTML output from the canonical sections and blocks.
        """
        sections = await RenderingPipeline.get_version_tree(db, version_id)
        
        html_lines = ["<article class='report-document'>"]
        for section in sections:
            html_lines.append(f"  <section id='{section.stable_id}'>")
            html_lines.append(f"    <h2>{section.title}</h2>")
            
            for block in section.blocks:
                b_id = f"id='{block.stable_id}'"
                if block.block_type == BlockContentType.paragraph:
                    content = block.markdown or (block.content_json.get("text") if block.content_json else "")
                    html_lines.append(f"    <p {b_id}>{content}</p>")
                elif block.block_type == BlockContentType.quote:
                    content = block.markdown.lstrip('>') if block.markdown else ""
                    html_lines.append(f"    <blockquote {b_id}>{content.strip()}</blockquote>")
                elif block.block_type == BlockContentType.list:
                    # simplistic fallback
                    html_lines.append(f"    <div {b_id} class='list-block'>{block.markdown}</div>")
                else:
                    html_lines.append(f"    <div {b_id} class='generic-block'>{block.markdown}</div>")
                    
            html_lines.append("  </section>")
            
        html_lines.append("</article>")
        return "\n".join(html_lines)
        
    @staticmethod
    async def validate_html(html_content: str) -> dict:
        """
        HTML Quality Engine validator.
        """
        errors = []
        if "<article" not in html_content:
            errors.append("Missing semantic <article> root.")
        if "id=" not in html_content:
            errors.append("Missing stable IDs for anchors.")
            
        return {
            "valid": len(errors) == 0,
            "errors": errors
        }

    @staticmethod
    async def execute_pipeline(db: AsyncSession, version_id: uuid.UUID):
        """
        Executes the full rendering pipeline and validation.
        """
        logger.info(f"Executing rendering pipeline for version {version_id}")
        
        md_content = await RenderingPipeline.render_markdown(db, version_id)
        html_content = await RenderingPipeline.render_html(db, version_id)
        
        validation = await RenderingPipeline.validate_html(html_content)
        if not validation["valid"]:
            logger.error(f"HTML Validation failed: {validation['errors']}")
            # In strict mode, we might throw an error or mark version as invalid.
            
        # PDF rendering would happen here using WeasyPrint or similar on the generated HTML
        
        # Here we would upload artifacts to R2 via the StorageProvider
        return {
            "markdown_length": len(md_content),
            "html_length": len(html_content),
            "html_valid": validation["valid"]
        }

rendering_pipeline = RenderingPipeline()
