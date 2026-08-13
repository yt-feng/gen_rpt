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
import re
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
from gen_rpt.web_publication_contract import clean_client_text, output_leak_hits


PDF_RENDERER_REVISION = "pdf-release-preview-v2"


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
    images = content.get("images", [])

    # Map image filenames to their presigned URLs for fallback rendering
    img_map = {img.get("key"): img.get("url") for img in images if img.get("key")}

    section_html = ""
    for idx, sec in enumerate(sections, start=1):
        heading = sec.get("heading", "")
        body = sec.get("body", "")
        is_disclaimer = sec.get("isDisclaimer", False)
        style = "font-size: 8pt; color: #666;" if is_disclaimer else ""
        tag = "p" if is_disclaimer else "div"

        # Check if there is an exhibit image for this section
        img_url = None
        for ext in ("png", "jpg", "jpeg"):
            candidate = f"image-{idx}.{ext}"
            if candidate in img_map:
                img_url = img_map[candidate]
                break

        img_html = ""
        if img_url:
            img_html = f"""
            <figure style="margin: 16pt 0; text-align: center; page-break-inside: avoid;">
                <img src="{img_url}" style="max-width: 100%; max-height: 280pt; object-fit: contain; border-radius: 6px; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,0.05);" />
            </figure>
            """

        section_html += f"""
        <section style="margin-bottom: 24pt; page-break-inside: avoid;">
            <h2 style="font-size: 13pt; font-weight: bold; color: #1a1a2e; margin-bottom: 8pt;
                       border-bottom: 1px solid #e0e0e0; padding-bottom: 4pt;">{heading}</h2>
            {img_html}
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
    # Deprecated xhtml2pdf function, no longer used
    pass


async def _generate_pdf_via_playwright(html_content: str) -> bytes:
    """
    Converts HTML to PDF bytes using Playwright.
    """
    import re
    clean_html = re.sub(r"\[Chunk:\s*[^\]]+\]\s*", "", str(html_content or ""), flags=re.I)
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(clean_html, wait_until="networkidle")
        pdf_bytes = await page.pdf(format="A4", print_background=True)
        await browser.close()
        return pdf_bytes


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _render_checksum(html_content: str) -> str:
    """Invalidate previews when output-safety behavior changes."""
    return _checksum(f"{PDF_RENDERER_REVISION}\0{html_content}".encode("utf-8"))


def _release_leak_hits(text: str) -> list[str]:
    value = str(text or "")
    hits = list(output_leak_hits(value))
    for pattern in (
        r"\b(?:chunk_id|why_it_matters|retrieval_score|embedding_metadata)\b",
        r"\bSupporting document evidence\b",
        r"[\{\[]\s*['\"][A-Za-z_][^'\"]*['\"]\s*:",
    ):
        if re.search(pattern, value, re.I):
            hits.append(pattern)
    return list(dict.fromkeys(hits))


def _sanitize_release_html(html_content: str, *, language: str = "en") -> str:
    """Remove internal evidence records from the user-facing preview HTML."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(str(html_content or ""), "html.parser")
    english_report = not str(language or "en").lower().startswith("zh")
    evidence_selector = ".evidence-list li, .evidence li, [class*='evidence'] li"

    for node in list(soup.select(evidence_selector)):
        text = node.get_text(" ", strip=True)
        source_language_mismatch = english_report and bool(re.search(r"[\u3400-\u9fff]{4,}", text))
        if _release_leak_hits(text) or source_language_mismatch:
            node.decompose()

    # Catch a serialized object that entered an ordinary paragraph or table cell.
    for node in list(soup.find_all(["p", "li", "blockquote", "pre", "code", "td", "dd"])):
        if node.parent is None:
            continue
        if _release_leak_hits(node.get_text(" ", strip=True)):
            node.decompose()

    for container in list(soup.select(".evidence-list, .evidence")):
        if not container.get_text(" ", strip=True):
            container.decompose()

    for node in soup.find_all(string=True):
        if node.parent and node.parent.name not in {"script", "style"}:
            raw = str(node)
            cleaned = clean_client_text(raw)
            if cleaned != raw.strip() and raw.strip():
                leading = raw[: len(raw) - len(raw.lstrip())]
                trailing = raw[len(raw.rstrip()) :]
                node.replace_with(f"{leading}{cleaned}{trailing}")

    visible = soup.get_text(" ", strip=True)
    leaks = _release_leak_hits(visible)
    if leaks:
        raise RuntimeError("PDF release HTML contains internal metadata: " + ", ".join(leaks))
    return str(soup)


def _validate_pdf_bytes(pdf_bytes: bytes) -> None:
    """Reject a PDF before upload if internal evidence is extractable from it."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise RuntimeError(f"Generated PDF could not be inspected: {exc}") from exc
    leaks = _release_leak_hits(text)
    if leaks:
        raise RuntimeError("Generated PDF contains internal metadata: " + ", ".join(leaks))


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
        language = report.get("language") or report.get("reportContent", {}).get("language") or "en"
        html_content = _sanitize_release_html(html_content, language=language)
        html_cs = _render_checksum(html_content)

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
        _validate_pdf_bytes(pdf_bytes)
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
        html_str = None
        # Try R2 snapshot HTML path (real mode)
        snapshot_html_path = report.get("snapshot_html_url")
        if snapshot_html_path:
            try:
                data = await storage_provider.download(snapshot_html_path)
                if data:
                    logger.info(f"[PdfRelease] HTML loaded from R2: {snapshot_html_path}")
                    html_str = data.decode("utf-8", errors="replace")
            except Exception as e:
                logger.warning(f"[PdfRelease] Could not load HTML from R2 ({snapshot_html_path}): {e}")

        # Try companion path derived from pdfPath
        if not html_str:
            pdf_path = report.get("pdfPath", "")
            if pdf_path.endswith(".pdf"):
                html_path = pdf_path.replace(".pdf", ".html")
                try:
                    data = await storage_provider.download(html_path)
                    if data:
                        logger.info(f"[PdfRelease] HTML loaded from R2 companion path: {html_path}")
                        html_str = data.decode("utf-8", errors="replace")
                except Exception as e:
                    logger.warning(f"[PdfRelease] Could not load companion HTML ({html_path}): {e}")

        # Try r2_prefix-based path (the new standard for generated HTML)
        if not html_str:
            r2_prefix = report.get("r2_prefix")
            if r2_prefix:
                web_html_path = f"{r2_prefix}current/index.html"
                try:
                    data = await storage_provider.download(web_html_path)
                    if data:
                        logger.info(f"[PdfRelease] HTML loaded from R2 prefix path: {web_html_path}")
                        html_str = data.decode("utf-8", errors="replace")
                except Exception as e:
                    logger.warning(f"[PdfRelease] Could not load R2 prefix HTML ({web_html_path}): {e}")
                    
        # Try reports_web slug-based path (legacy fallback)
        if not html_str:
            slug = report.get("slug") or report_id
            web_html_path = f"reports_web/{slug}/index.html"
            try:
                data = await storage_provider.download(web_html_path)
                if data:
                    logger.info(f"[PdfRelease] HTML loaded from reports_web path: {web_html_path}")
                    html_str = data.decode("utf-8", errors="replace")
            except Exception as e:
                logger.warning(f"[PdfRelease] Could not load reports_web HTML ({web_html_path}): {e}")

        if html_str:
            return await self._inject_html_content(html_str, report)

        # Fallback: build from report dict
        logger.info(f"[PdfRelease] Building HTML from report dict for {report_id}")
        return _build_html_from_report(report)

    async def _inject_html_content(self, html_str: str, report: dict) -> str:
        slug = report.get("slug") or report.get("id") or "doc-1111-approved"
        
        try:
            from bs4 import BeautifulSoup
            import markdown
            soup = BeautifulSoup(html_str, "html.parser")
            
            # 1. Replace relative image paths with R2 presigned URLs
            r2_prefix = report.get("r2_prefix")
            for img in soup.find_all("img"):
                src = img.get("src")
                if src and not src.startswith("http") and not src.startswith("data:"):
                    # Use r2_prefix if available, else fallback to reports_web/{slug}
                    r2_path = f"{r2_prefix}current/{src}" if r2_prefix else f"reports_web/{slug}/{src}"
                    signed_url = await storage_provider.get_signed_url(r2_path, expiration_sec=3600)
                    if signed_url:
                        img["src"] = signed_url
                        
            # 2. Inject updated text content
            report_text = report.get("reportContent", {}).get("text")
            if not report_text:
                sections = report.get("reportContent", {}).get("sections", [])
                if sections:
                    markdown_lines = []
                    for sec in sections:
                        h = sec.get("heading", "")
                        b = sec.get("body", "")
                        if h:
                            markdown_lines.append(f"## {h}\n")
                        markdown_lines.append(f"{b}\n")
                    report_text = "\n".join(markdown_lines)

            if report_text:
                article = soup.find("article", class_="article-main")
                if article:
                    html_sections = soup.find_all("section", class_="section-block")
                    sections_in_report = report.get("reportContent", {}).get("sections", [])

                    if html_sections and sections_in_report:
                        # Surgical in-place replacement to preserve all layout, kickers, list items,
                        # custom styles, figures, and most importantly: the images (figures)!
                        import re
                        def heading_slug(heading: str) -> str:
                            return re.sub(r"[^\w]+", "-", heading.lower()).strip("-")

                        for idx, html_sec in enumerate(html_sections):
                            if idx >= len(sections_in_report):
                                break
                            sec = sections_in_report[idx]
                            report_paras = [p.strip() for p in sec.get("body", "").split("\n\n") if p.strip()]

                            # Collect all paragraphs that are NOT lead text blocks
                            html_ps = [p for p in html_sec.find_all("p") if "section-lead" not in p.get("class", [])]

                            # Replace text in-place / insert new paragraphs if count changes
                            for p_idx, text in enumerate(report_paras):
                                if p_idx < len(html_ps):
                                    html_ps[p_idx].string = text
                                else:
                                    new_p = soup.new_tag("p")
                                    new_p.string = text
                                    if html_ps:
                                        html_ps[-1].insert_after(new_p)
                                    else:
                                        html_sec.append(new_p)
                                    html_ps.append(new_p)

                            # Remove extra paragraphs if text has been deleted
                            if len(html_ps) > len(report_paras):
                                for extra_p in html_ps[len(report_paras):]:
                                    extra_p.decompose()
                    else:
                        # Fallback for reports with standard flat article formats
                        md_html = markdown.markdown(report_text)
                        article.clear()
                        new_content = BeautifulSoup(md_html, "html.parser")
                        article.append(new_content)
                    
            return str(soup)
        except Exception as e:
            logger.warning(f"[PdfRelease] Failed to inject dynamic content/images: {e}")
            
        return html_str

    async def _generate_pdf(self, html_content: str) -> bytes:
        import re
        clean_html = re.sub(r"\[Chunk:\s*[^\]]+\]\s*", "", str(html_content or ""), flags=re.I)
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, 
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            page = await browser.new_page()
            await page.set_content(clean_html, wait_until="load")
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "2cm", "bottom": "2cm", "left": "2.5cm", "right": "2.5cm"}
            )
            await browser.close()
            return pdf_bytes

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
