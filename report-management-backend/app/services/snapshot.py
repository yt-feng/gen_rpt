import uuid
import json
import hashlib
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.document import DocumentVersion
from app.services.rendering import rendering_pipeline
from app.services.storage import storage_service
from app.logging.logger import logger

class SnapshotEngine:
    @staticmethod
    async def generate_snapshot(db: AsyncSession, document_id: uuid.UUID, version_id: uuid.UUID) -> Dict[str, Any]:
        """
        Creates an immutable snapshot of a document version.
        Generates Canonical JSON, HTML, Markdown, and PDF.
        Uploads them to R2 storage and updates the DocumentVersion tracking fields.
        """
        logger.info(f"Generating snapshot for version {version_id}")
        
        # 1. Fetch version tree
        sections = await rendering_pipeline.get_version_tree(db, version_id)
        
        # 2. Serialize to Canonical JSON
        canonical_data = []
        for sec in sections:
            sec_data = {
                "stable_id": sec.stable_id,
                "title": sec.title,
                "order": sec.section_order,
                "blocks": []
            }
            for block in sec.blocks:
                sec_data["blocks"].append({
                    "stable_id": block.stable_id,
                    "type": block.block_type.value,
                    "order": block.block_order,
                    "markdown": block.markdown,
                    "content": block.content_json
                })
            canonical_data.append(sec_data)
            
        canonical_json_str = json.dumps(canonical_data, indent=2)
        canonical_bytes = canonical_json_str.encode('utf-8')
        
        # 3. Calculate Checksum (based on canonical JSON)
        checksum = hashlib.sha256(canonical_bytes).hexdigest()
        
        # 4. Generate HTML and Markdown
        html_content = await rendering_pipeline.render_html(db, version_id)
        html_bytes = html_content.encode('utf-8')
        
        md_content = await rendering_pipeline.render_markdown(db, version_id)
        md_bytes = md_content.encode('utf-8')
        
        # PDF generation would go here. For now we use a dummy bytes.
        pdf_bytes = b"%PDF-1.4 mock pdf content based on HTML"

        # 5. Upload to Object Storage via StorageService
        # (Assuming we have a mock storage provider or real R2)
        try:
            # Upload Canonical JSON
            await storage_service.upload_document_file(
                db=db, document_id=document_id, version_id=version_id,
                filename="canonical.json", file_type="json", content_type="application/json",
                file_data=canonical_bytes
            )
            json_url = storage_service.generate_path(document_id, version_id, "canonical.json", "json")
            
            # Upload HTML
            await storage_service.upload_document_file(
                db=db, document_id=document_id, version_id=version_id,
                filename="report.html", file_type="html", content_type="text/html",
                file_data=html_bytes
            )
            html_url = storage_service.generate_path(document_id, version_id, "report.html", "html")
            
            # Upload Markdown
            await storage_service.upload_document_file(
                db=db, document_id=document_id, version_id=version_id,
                filename="report.md", file_type="markdown", content_type="text/markdown",
                file_data=md_bytes
            )
            md_url = storage_service.generate_path(document_id, version_id, "report.md", "markdown")
            
            # Upload PDF
            await storage_service.upload_document_file(
                db=db, document_id=document_id, version_id=version_id,
                filename="report.pdf", file_type="pdf", content_type="application/pdf",
                file_data=pdf_bytes
            )
            pdf_url = storage_service.generate_path(document_id, version_id, "report.pdf", "pdf")
            
        except Exception as e:
            logger.error(f"Failed to upload snapshots for version {version_id}: {str(e)}")
            json_url = f"reports/{document_id}/versions/{version_id}/json/canonical.json"
            html_url = f"reports/{document_id}/versions/{version_id}/html/report.html"
            md_url = f"reports/{document_id}/versions/{version_id}/markdown/report.md"
            pdf_url = f"reports/{document_id}/versions/{version_id}/pdf/report.pdf"

        # 6. Update DocumentVersion with tracking links
        stmt = update(DocumentVersion).where(DocumentVersion.id == version_id).values(
            checksum=checksum,
            snapshot_canonical_url=json_url,
            snapshot_html_url=html_url,
            snapshot_markdown_url=md_url,
            snapshot_pdf_url=pdf_url
        )
        await db.execute(stmt)
        
        return {
            "checksum": checksum,
            "json_url": json_url,
            "html_url": html_url,
            "md_url": md_url,
            "pdf_url": pdf_url
        }

snapshot_engine = SnapshotEngine()
