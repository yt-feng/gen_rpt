"""
app/services/pdf_release.py

PDF Release Service — Versioned PDF generation and reuse for publication previews.

Responsibilities:
  - Determine whether a valid, up-to-date PDF already exists for a document.
  - Generate a new PDF from the report HTML if content has changed.
  - Store the PDF as an immutable versioned artifact in Cloudflare R2.
  - Create a metadata record (PdfRelease) for audit and version tracking.
  - Return a presigned URL valid for 1 hour so the frontend can render the preview.

This service does NOT publish. Publishing remains entirely in publish_orchestrator.py.
"""

import hashlib
import io
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.pdf_release import PdfRelease
from app.storage.provider import storage_provider
from app.logging.logger import logger


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class PdfReleaseResult:
    pdf_release_id: str
    version_number: int
    is_new: bool               # True = freshly generated, False = reused existing
    storage_path: str
    preview_url: str           # Presigned R2 GET URL, valid for 1 hour
    file_size_bytes: int
    generated_at: str          # ISO 8601
    html_checksum: str
    document_version: str      # Human-readable version label from report dict


# ---------------------------------------------------------------------------
# HTML rendering helpers
# ---------------------------------------------------------------------------

def _build_html_from_report(report: dict) -> str:
    """
    Builds a print-quality HTML document from the MOCK_REPORTS report dict.
    Used when the source HTML is not available in R2 (mock mode).
    Preserves: title, version, sections (heading + body), disclaimer.
    """
    title = report.get("title", "Report")
    version = report.get("version", "v1")
    content = report.get("reportContent", {})
    brand = content.get("brand", "Thought Leadership")
    date_str = content.get("date", datetime.now(timezone.utc).strftime("%B %d, %Y"))
    sections = content.get("sections", [])

    section_html = ""
    for sec in sections:
        heading = sec.get("heading", "")
        body = sec.get("body", "")
        is_disclaimer = sec.get("isDisclaimer", False)
        style = "font-size: 8pt; color: #666;" if is_disclaimer else ""
        tag = "p" if is_disclaimer else "div"
        section_html += f"""
        <section style="margin-bottom: 24pt; page-break-inside: avoid;">
            <h2 style="font-size: 13pt; font-weight: bold; color: #1a1a2e; margin-bottom: 8pt;
                       border-bottom: 1px solid #e0e0e0; padding-bottom: 4pt;">{heading}</h2>
            <{tag} style="font-size: 10pt; line-height: 1.6; color: #333; {style}">{body}</{tag}>
        </section>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>{title}</title>
  <style>
    @page {{
      size: A4;
      margin: 2cm 2.5cm;
    }}
    body {{
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 10pt;
      color: #1a1a1a;
      line-height: 1.6;
    }}
    .cover {{
      text-align: center;
      padding-top: 80pt;
      margin-bottom: 40pt;
    }}
    .cover .brand-label {{
      font-size: 9pt;
      text-transform: uppercase;
      letter-spacing: 2pt;
      color: #666;
      margin-bottom: 16pt;
    }}
    .cover h1 {{
      font-size: 22pt;
      font-weight: 800;
      color: #0f172a;
      line-height: 1.3;
      margin-bottom: 12pt;
    }}
    .cover .meta {{
      font-size: 9pt;
      color: #888;
    }}
    hr {{
      border: none;
      border-top: 1px solid #e0e0e0;
      margin: 20pt 0;
    }}
  </style>
</head>
<body>
  <div class="cover">
    <div class="brand-label">{brand}</div>
    <h1>{title}</h1>
    <div class="meta">Version {version} &nbsp;|&nbsp; {date_str}</div>
  </div>
  <hr/>
  {section_html}
</body>
</html>"""


def _generate_pdf_bytes(html_content: str) -> bytes:
    """
    Converts HTML to PDF bytes using xhtml2pdf (pure-Python, no system deps).
    Returns raw PDF bytes.
    """
    from xhtml2pdf import pisa  # type: ignore

    buf = io.BytesIO()
    pisa_status = pisa.CreatePDF(html_content, dest=buf)
    if pisa_status.err:
        raise RuntimeError(f"PDF generation failed with {pisa_status.err} error(s).")
    return buf.getvalue()


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _r2_pdf_path(document_id: str, version_number: int) -> str:
    """
    Immutable versioned R2 path.
    Example: reports/doc-1111-approved/versions/pdf/v3/report.pdf
    """
    return f"reports/{document_id}/versions/pdf/v{version_number}/report.pdf"


# ---------------------------------------------------------------------------
# PDF Release Service
# ---------------------------------------------------------------------------

class PdfReleaseService:

    # ------------------------------------------------------------------
    # Public entry point: get or generate a PDF for a document
    # ------------------------------------------------------------------
    async def get_or_generate(
        self,
        db: AsyncSession,
        report_id: str,
        report: dict,
        actor_id: str,
    ) -> PdfReleaseResult:
        """
        Main entry point. Called when the user clicks "Publish Report Instantly".
        
        1. Build the HTML for this report.
        2. Compute the HTML checksum.
        3. Look up the latest active PdfRelease for this document.
        4. If checksum matches → reuse. Otherwise → generate a new versioned PDF.
        5. Return preview metadata including a presigned URL.
        """
        start = time.monotonic()

        # Step 1: Resolve HTML
        html_content = await self._resolve_html(report_id, report)
        html_bytes = html_content.encode("utf-8")
        html_cs = _checksum(html_bytes)

        # Step 2: Resolve document UUID
        doc_uuid = self._to_uuid(report_id)

        # Step 3: Look up latest active record
        existing = await self._get_latest_active(db, doc_uuid)

        if existing and existing.html_checksum == html_cs:
            # ── Reuse existing PDF ──────────────────────────────────────
            logger.info(f"[PdfRelease] Reusing PDF v{existing.version_number} for {report_id} (checksum match)")
            preview_url = await storage_provider.get_signed_url(existing.storage_path, expiration_sec=3600)
            return PdfReleaseResult(
                pdf_release_id=str(existing.id),
                version_number=existing.version_number,
                is_new=False,
                storage_path=existing.storage_path,
                preview_url=preview_url,
                file_size_bytes=existing.file_size_bytes or 0,
                generated_at=existing.generated_at.isoformat(),
                html_checksum=existing.html_checksum or "",
                document_version=report.get("version", "v1"),
            )

        # ── Generate a new PDF ──────────────────────────────────────────
        next_version = (existing.version_number + 1) if existing else 1
        logger.info(f"[PdfRelease] Generating PDF v{next_version} for {report_id}")

        # Generate
        pdf_bytes = await self._generate_pdf(html_content)
        render_ms = int((time.monotonic() - start) * 1000)

        # Store in R2
        r2_path = _r2_pdf_path(report_id, next_version)
        uploaded = await storage_provider.upload(pdf_bytes, r2_path, "application/pdf")
        if not uploaded:
            raise RuntimeError(f"Failed to upload PDF to R2 at path: {r2_path}")

        # Deactivate previous record
        if existing:
            await db.execute(
                update(PdfRelease)
                .where(PdfRelease.document_id == doc_uuid, PdfRelease.is_active == True)
                .values(is_active=False)
            )

        # Create new PdfRelease record
        actor_uuid = self._to_uuid_or_none(actor_id)

        # Ensure document row exists (mock mode safety)
        await self._ensure_document_exists(db, doc_uuid, report)

        record = PdfRelease(
            document_id=doc_uuid,
            version_number=next_version,
            html_checksum=html_cs,
            canonical_version_label=report.get("version", "v1"),
            storage_path=r2_path,
            file_size_bytes=len(pdf_bytes),
            render_duration_ms=render_ms,
            generated_by=actor_uuid,
            generated_at=datetime.now(timezone.utc),
            is_active=True,
            gatex_published_version=False,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

        preview_url = await storage_provider.get_signed_url(r2_path, expiration_sec=3600)

        logger.info(
            f"[PdfRelease] PDF v{next_version} generated in {render_ms}ms, "
            f"size={len(pdf_bytes)} bytes, path={r2_path}"
        )

        return PdfReleaseResult(
            pdf_release_id=str(record.id),
            version_number=next_version,
            is_new=True,
            storage_path=r2_path,
            preview_url=preview_url,
            file_size_bytes=len(pdf_bytes),
            generated_at=record.generated_at.isoformat(),
            html_checksum=html_cs,
            document_version=report.get("version", "v1"),
        )

    # ------------------------------------------------------------------
    # Get the latest active PDF release for a document (for future reuse)
    # ------------------------------------------------------------------
    async def get_latest_for_document(
        self,
        db: AsyncSession,
        document_id: str,
    ) -> Optional[PdfRelease]:
        doc_uuid = self._to_uuid(document_id)
        return await self._get_latest_active(db, doc_uuid)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _resolve_html(self, report_id: str, report: dict) -> str:
        """
        Attempts to fetch the HTML from R2 (snapshot_html_url or companion path).
        Falls back to building HTML from the report dict (mock mode).
        """
        # Try R2 snapshot HTML path (real mode)
        snapshot_html_path = report.get("snapshot_html_url")
        if snapshot_html_path:
            try:
                data = await storage_provider.download(snapshot_html_path)
                if data:
                    logger.info(f"[PdfRelease] HTML loaded from R2: {snapshot_html_path}")
                    return data.decode("utf-8", errors="replace")
            except Exception as e:
                logger.warning(f"[PdfRelease] Could not load HTML from R2 ({snapshot_html_path}): {e}")

        # Try companion path derived from pdfPath
        pdf_path = report.get("pdfPath", "")
        if pdf_path.endswith(".pdf"):
            html_path = pdf_path.replace(".pdf", ".html")
            try:
                data = await storage_provider.download(html_path)
                if data:
                    logger.info(f"[PdfRelease] HTML loaded from R2 companion path: {html_path}")
                    return data.decode("utf-8", errors="replace")
            except Exception as e:
                logger.warning(f"[PdfRelease] Could not load companion HTML ({html_path}): {e}")

        # Try reports_web slug-based path
        slug = report.get("slug") or report_id
        web_html_path = f"reports_web/{slug}/index.html"
        try:
            data = await storage_provider.download(web_html_path)
            if data:
                logger.info(f"[PdfRelease] HTML loaded from reports_web path: {web_html_path}")
                return data.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"[PdfRelease] Could not load reports_web HTML ({web_html_path}): {e}")

        # Fallback: build from report dict
        logger.info(f"[PdfRelease] Building HTML from report dict for {report_id}")
        return _build_html_from_report(report)

    async def _generate_pdf(self, html_content: str) -> bytes:
        """Runs xhtml2pdf in a thread to avoid blocking the event loop."""
        from anyio import to_thread
        return await to_thread.run_sync(_generate_pdf_bytes, html_content)

    async def _get_latest_active(
        self, db: AsyncSession, doc_uuid: uuid.UUID
    ) -> Optional[PdfRelease]:
        stmt = (
            select(PdfRelease)
            .where(PdfRelease.document_id == doc_uuid, PdfRelease.is_active == True)
            .order_by(PdfRelease.version_number.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _ensure_document_exists(self, db: AsyncSession, doc_uuid: uuid.UUID, report: dict):
        """Ensures a Document row exists for mock-mode reports."""
        from app.models.document import Document
        from sqlalchemy import select as sa_select
        exists = await db.scalar(sa_select(Document.id).where(Document.id == doc_uuid))
        if not exists:
            dummy = Document(
                id=doc_uuid,
                title=report.get("title", f"Report {doc_uuid}"),
                slug=f"report-{str(doc_uuid)[:8]}",
                status="draft",
            )
            db.add(dummy)
            await db.flush()

    @staticmethod
    def _to_uuid(raw_id: str) -> uuid.UUID:
        try:
            return uuid.UUID(raw_id)
        except ValueError:
            m = hashlib.md5()
            m.update(raw_id.encode("utf-8"))
            return uuid.UUID(m.hexdigest())

    @staticmethod
    def _to_uuid_or_none(raw_id: str) -> Optional[uuid.UUID]:
        try:
            return uuid.UUID(raw_id)
        except (ValueError, TypeError):
            return None


pdf_release_service = PdfReleaseService()
