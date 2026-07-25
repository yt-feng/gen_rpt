"""
GateX Publish Orchestrator
===========================
Coordinates the end-to-end publish flow from an internal report to GateX (MENA Compass).

Full 15-step pipeline:
  1.  Validate report eligibility
  2.  Check for duplicate publication
  3.  Mark report as "publishing" in internal state
  4.  Fetch PDF bytes from Cloudflare R2
  5.  Fetch cover image bytes from Cloudflare R2
  6.  Request GateX presigned URL for PDF   (REPORT_ORIGINAL)
  7.  Upload PDF to GateX storage
  8.  Request GateX presigned URL for cover  (REPORT_IMAGE)
  9.  Upload cover image to GateX storage
  10. Resolve GateX category / tag / region IDs
  11. Build GateX report metadata payload
  12. Submit to POST /api/reports/bulk
  13. Store external identifiers in GateXPublication record
  14. Mark report as Published in internal state + MOCK_REPORTS
  15. Write audit logs for every major step

On any failure after files are uploaded (steps 7+), the orchestrator:
  - Marks the report as "publish_failed"
  - Preserves the object keys already uploaded (for safe retry)
  - Records the error in the GateXPublication record
  - Records audit log for the failure event

This module does NOT modify any report generation, AI review, versioning,
canonical document, HTML synchronization, or workflow engine logic.
"""

import uuid
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.logging.logger import logger
from app.models.workflow import GateXPublication
from app.services.gatex import (
    gatex_client,
    GateXReportPayload,
    GateXSubmitResult,
    GateXDisabledError,
    GateXError,
    GateXAuthError,
    GateXValidationError,
)
from app.services import gatex_taxonomy
from app.storage.provider import storage_provider
from app.services.audit import audit_service


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class EligibilityResult:
    eligible: bool
    reasons: List[str] = field(default_factory=list)


@dataclass
class PublishResult:
    success: bool
    report_id: str
    external_report_id: Optional[int] = None
    external_status: Optional[str] = None
    processing_status: Optional[str] = None
    publish_status: str = "publish_failed"
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    audit_trail: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class PublishOrchestrator:
    """
    Stateless orchestrator. All state is persisted to Supabase via the
    GateXPublication model and the existing MOCK_REPORTS dict (for the
    mock-mode frontend).
    """

    # ------------------------------------------------------------------
    # Step 1: Eligibility validation
    # ------------------------------------------------------------------
    def validate_eligibility(self, report: dict) -> EligibilityResult:
        """
        Checks all eligibility rules before initiating the publish flow.
        Returns a result object with pass/fail and a list of failure reasons.
        """
        reasons = []

        status = report.get("status", "")
        if status not in ("Approved", "approved", "Generated", "generated"):
            reasons.append(f"Report status must be 'Approved' or 'Generated', got '{status}'.")

        # if report.get("publishReady") is False:
        #     reasons.append("Report is not marked as publish-ready.")

        # Check for mock-mode eligibility markers
        # In the real system, these would check DocumentFile records and version sync flags.
        # For now we rely on the status field being Approved.

        return EligibilityResult(eligible=len(reasons) == 0, reasons=reasons)

    # ------------------------------------------------------------------
    # Step 2: Duplicate protection
    # ------------------------------------------------------------------
    async def check_duplicate(self, db: AsyncSession, document_id: str) -> Optional[GateXPublication]:
        """
        Returns an existing publication record if the report has already been
        published or is currently being published. Returns None if safe to proceed.
        """
        try:
            doc_uuid = uuid.UUID(document_id)
        except ValueError:
            return None

        stmt = (
            select(GateXPublication)
            .where(GateXPublication.document_id == doc_uuid)
            .where(GateXPublication.publish_status.in_(["published", "publishing", "external_sync_pending"]))
        )
        result = await db.execute(stmt)
        record = result.scalars().first()
        if record:
            # If already fully published or in-progress, block duplicate
            if record.publish_status in ("published", "external_sync_pending", "publishing"):
                return record
        return None

    # ------------------------------------------------------------------
    # Helper: resolve or create a GateXPublication record
    # ------------------------------------------------------------------
    async def _create_publication_record(
        self,
        db: AsyncSession,
        document_id: str,
        published_by: str,
    ) -> GateXPublication:
        try:
            doc_uuid = uuid.UUID(document_id)
        except ValueError:
            import hashlib
            m = hashlib.md5()
            m.update(document_id.encode("utf-8"))
            doc_uuid = uuid.UUID(m.hexdigest())

        try:
            actor_uuid = uuid.UUID(published_by)
        except (ValueError, TypeError):
            actor_uuid = None

        # Ensure a Document exists for this ID (crucial for mock mode)
        from app.models.document import Document
        from sqlalchemy import select
        doc_exists = await db.scalar(select(Document.id).where(Document.id == doc_uuid))
        if not doc_exists:
            dummy_doc = Document(
                id=doc_uuid,
                title=f"Mock Report {document_id}",
                slug=f"mock-{doc_uuid}",
                status="draft"
            )
            db.add(dummy_doc)
            await db.flush()

        record = GateXPublication(
            document_id=doc_uuid,
            publish_status="publishing",
            published_by=actor_uuid,
        )
        db.add(record)
        await db.flush()
        return record

    # ------------------------------------------------------------------
    # Helper: fetch file bytes from R2 via DocumentFile lookup
    # ------------------------------------------------------------------
    async def _fetch_r2_bytes(self, r2_path: str, label: str) -> bytes:
        """Fetches raw bytes from Cloudflare R2 for a given path."""
        logger.info(f"Fetching {label} from R2: {r2_path}")
        data = await storage_provider.download(r2_path)
        if not data:
            raise GateXError(f"Could not fetch {label} from R2 path: {r2_path}")
        return data

    # ------------------------------------------------------------------
    # Helper: resolve PDF and cover image R2 paths from mock report
    # ------------------------------------------------------------------
    async def _resolve_file_paths(self, db: AsyncSession, report: dict, report_id: str) -> Dict[str, Optional[str]]:
        """
        Resolves R2 paths for PDF and cover image.
        In the current mock-mode system, we look for these keys in the report dict.
        In production, these would be retrieved from DocumentFile records.
        """
        pdf_path = report.get("pdfPath") or report.get("snapshot_pdf_url")
        
        # Override with latest PdfRelease storage path if available
        try:
            import uuid
            import hashlib
            from sqlalchemy import select
            from app.models.pdf_release import PdfRelease
            
            try:
                doc_uuid = uuid.UUID(report_id)
            except ValueError:
                m = hashlib.md5()
                m.update(report_id.encode("utf-8"))
                doc_uuid = uuid.UUID(m.hexdigest())

            stmt = select(PdfRelease.storage_path).where(
                PdfRelease.document_id == doc_uuid,
                PdfRelease.is_active == True
            )
            res = await db.scalar(stmt)
            if res:
                pdf_path = res
        except Exception as e:
            logger.warning(f"Failed to fetch latest PdfRelease path for {report_id}: {e}")

        return {
            "pdf_path": pdf_path,
            "cover_path": report.get("coverImagePath") or settings.GATEX_DEFAULT_COVER_PATH or None,
        }

    # ------------------------------------------------------------------
    # Step 3–14: Full publish pipeline
    # ------------------------------------------------------------------
    async def publish(
        self,
        db: AsyncSession,
        report: dict,
        report_id: str,
        actor_id: str,
    ) -> PublishResult:
        """
        Executes the full 15-step GateX publish pipeline.
        Preserves all existing workflows — this only appends external publishing logic.
        """
        start_time = time.time()
        audit_trail: List[str] = []
        pub_record: Optional[GateXPublication] = None

        def _audit(msg: str):
            audit_trail.append(msg)
            logger.info(f"[GateX Publish | {report_id}] {msg}")

        def _elapsed_ms() -> int:
            return int((time.time() - start_time) * 1000)

        # ---- Step 1: Eligibility ----
        eligibility = self.validate_eligibility(report)
        if not eligibility.eligible:
            msg = "; ".join(eligibility.reasons)
            _audit(f"Eligibility check FAILED: {msg}")
            return PublishResult(
                success=False,
                report_id=report_id,
                publish_status="publish_failed",
                error=msg,
                audit_trail=audit_trail,
            )
        _audit("Eligibility check PASSED.")

        # ---- Step 2: Duplicate protection ----
        existing = await self.check_duplicate(db, report_id)
        if existing:
            msg = (
                f"Duplicate publish blocked: report already has status "
                f"'{existing.publish_status}' (external_id={existing.external_report_id})."
            )
            _audit(msg)
            return PublishResult(
                success=False,
                report_id=report_id,
                publish_status=existing.publish_status,
                external_report_id=existing.external_report_id,
                error=msg,
                audit_trail=audit_trail,
            )
        _audit("Duplicate protection check PASSED.")

        # ---- Step 3: Mark as publishing ----
        pub_record = await self._create_publication_record(db, report_id, actor_id)
        _audit(f"Publication record created: internal_id={pub_record.id}")

        # ---- Disabled check (after eligibility so the error is clear) ----
        if not settings.GATEX_ENABLE_PUBLISHING:
            _audit("GATEX_ENABLE_PUBLISHING=false — dry-run mode. No external calls made.")
            pub_record.publish_status = "publish_failed"
            pub_record.errors = "Publishing is disabled (GATEX_ENABLE_PUBLISHING=false)."
            pub_record.publish_duration_ms = _elapsed_ms()
            await db.commit()
            return PublishResult(
                success=False,
                report_id=report_id,
                publish_status="publish_failed",
                error="GateX publishing is disabled. Set GATEX_ENABLE_PUBLISHING=true to enable.",
                audit_trail=audit_trail,
                duration_ms=_elapsed_ms(),
            )

        try:
            # ---- Steps 4–5: Fetch files from R2 ----
            paths = await self._resolve_file_paths(db, report, report_id)
            pdf_path = paths.get("pdf_path")
            cover_path = paths.get("cover_path")

            if not pdf_path:
                raise GateXError("No PDF path found for this report. Ensure a PDF has been generated and stored in R2.")
            if not cover_path:
                raise GateXError(
                    "No cover image path found for this report. "
                    "Set GATEX_DEFAULT_COVER_PATH or ensure a cover image is stored in R2 under DocumentFile."
                )

            pdf_bytes = await self._fetch_r2_bytes(pdf_path, "report PDF")
            _audit(f"PDF fetched from R2: {len(pdf_bytes)} bytes, path={pdf_path}")

            cover_bytes = await self._fetch_r2_bytes(cover_path, "cover image")
            _audit(f"Cover image fetched from R2: {len(cover_bytes)} bytes, path={cover_path}")

            # ---- Step 6: Presign URL for PDF ----
            pdf_filename = pdf_path.split("/")[-1] or "report.pdf"
            pdf_presign = await gatex_client.get_presigned_url(
                key=pdf_filename,
                content_type="application/pdf",
                upload_type="REPORT_ORIGINAL",
                file_size=len(pdf_bytes),
            )
            pub_record.original_object_key = pdf_presign.key
            _audit(f"PDF presigned URL received: object_key={pdf_presign.key}")

            # ---- Step 7: Upload PDF ----
            await gatex_client.upload_file(pdf_presign, pdf_bytes)
            _audit(f"PDF uploaded to GateX storage: key={pdf_presign.key}")

            # ---- Step 8: Presign URL for cover image ----
            cover_filename = cover_path.split("/")[-1] or "cover.jpg"
            cover_ext = cover_filename.rsplit(".", 1)[-1].lower() if "." in cover_filename else "jpg"
            cover_mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}
            cover_mime = cover_mime_map.get(cover_ext, "image/jpeg")

            img_presign = await gatex_client.get_presigned_url(
                key=cover_filename,
                content_type=cover_mime,
                upload_type="REPORT_IMAGE",
                file_size=len(cover_bytes),
            )
            pub_record.cover_image_key = img_presign.key
            _audit(f"Cover image presigned URL received: object_key={img_presign.key}")

            # ---- Step 9: Upload cover image ----
            await gatex_client.upload_file(img_presign, cover_bytes)
            _audit(f"Cover image uploaded to GateX storage: key={img_presign.key}")

            # Flush object keys early in case metadata submission fails
            await db.flush()

            # ---- Step 10: Resolve taxonomy IDs ----
            industry = report.get("industry") or report.get("reportContent", {}).get("brand")
            category_id = await gatex_taxonomy.resolve_category_id(industry)
            if not category_id:
                raise GateXError(
                    "Could not resolve a GateX category ID. "
                    "Ensure GATEX_BASE_URL is set and report categories are available."
                )
            tag_ids = await gatex_taxonomy.resolve_tag_ids(tags=report.get("tags"))
            if not tag_ids:
                raise GateXError(
                    "Could not resolve any GateX tag IDs. "
                    "At least one tag is required per the API specification."
                )
            region_id = await gatex_taxonomy.resolve_region_id(report.get("region"))
            _audit(f"Taxonomy resolved: category_id={category_id} tag_ids={tag_ids} region_id={region_id}")

            # ---- Step 11: Build metadata payload ----
            payload = GateXReportPayload(
                title=report.get("title", "Untitled Report"),
                original_file_name=pdf_filename,
                mime_type="application/pdf",
                file_size=len(pdf_bytes),
                original_object_key=pdf_presign.key,
                top_image=img_presign.key,
                category_id=category_id,
                tag_ids=tag_ids,
                description=report.get("description") or report.get("humanStatus"),
                region_id=region_id,
                price=5800.0,   # GateX minimum price requirement
                is_featured=False,
                publish=True,  # Publish immediately in GateX
            )
            _audit("GateX report metadata payload built.")

            # ---- Step 12: Submit metadata ----
            result: GateXSubmitResult = await gatex_client.submit_bulk_report(payload)
            _audit(
                f"GateX bulk submission completed: success={result.success} "
                f"external_id={result.external_report_id} status={result.external_status}"
            )

            # ---- Step 13: Store external identifiers ----
            pub_record.external_report_id = result.external_report_id
            pub_record.external_response = result.raw_response
            pub_record.retry_count = 0
            pub_record.published_at = datetime.now(timezone.utc)
            pub_record.publish_duration_ms = _elapsed_ms()
            pub_record.last_synced_at = datetime.now(timezone.utc)

            if result.success:
                pub_record.publish_status = "published"
                _audit(f"External identifiers stored: external_id={result.external_report_id}")

                # ---- Step 14: Mark report as Published in MOCK_REPORTS ----
                # Import here to avoid circular imports
                from app.api.v1.endpoints.reports import MOCK_REPORTS
                if report_id in MOCK_REPORTS:
                    MOCK_REPORTS[report_id]["status"] = "Published"
                    MOCK_REPORTS[report_id]["publishReady"] = True
                    MOCK_REPORTS[report_id]["externalReportId"] = result.external_report_id
                    MOCK_REPORTS[report_id]["publishStatus"] = "published"
                _audit("Report marked as Published in internal state.")

                # ---- Step 15: Audit log ----
                try:
                    doc_uuid = uuid.UUID(report_id)
                except ValueError:
                    import hashlib
                    m = hashlib.md5()
                    m.update(report_id.encode("utf-8"))
                    doc_uuid = uuid.UUID(m.hexdigest())

                try:
                    actor_uuid = uuid.UUID(actor_id)
                except (ValueError, TypeError):
                    actor_uuid = None

                await audit_service.log_action(
                    db=db,
                    table_name="gatex_publications",
                    record_id=pub_record.id,
                    action="publish_success",
                    old_data={"status": report.get("status")},
                    new_data={
                        "status": "Published",
                        "external_report_id": result.external_report_id,
                        "publish_status": "external_sync_pending",
                    },
                    changed_by=actor_uuid,
                )
                _audit("Audit log recorded.")

                await db.commit()

                return PublishResult(
                    success=True,
                    report_id=report_id,
                    external_report_id=result.external_report_id,
                    external_status=result.external_status,
                    processing_status=result.processing_status,
                    publish_status="published",
                    duration_ms=_elapsed_ms(),
                    audit_trail=audit_trail,
                )
            else:
                # GateX returned 207 with failures
                pub_record.publish_status = "publish_failed"
                pub_record.errors = result.error_message
                await db.commit()
                _audit(f"GateX metadata submission failed: {result.error_message}")
                return PublishResult(
                    success=False,
                    report_id=report_id,
                    publish_status="publish_failed",
                    error=result.error_message,
                    duration_ms=_elapsed_ms(),
                    audit_trail=audit_trail,
                )

        except (GateXAuthError, GateXValidationError, GateXError) as e:
            logger.error(f"[GateX Publish | {report_id}] Error: {e}")
            _audit(f"Publish FAILED: {e}")
            if pub_record:
                pub_record.publish_status = "publish_failed"
                pub_record.errors = str(e)
                pub_record.publish_duration_ms = _elapsed_ms()
                await db.commit()
            return PublishResult(
                success=False,
                report_id=report_id,
                publish_status="publish_failed",
                error=str(e),
                duration_ms=_elapsed_ms(),
                audit_trail=audit_trail,
            )
        except Exception as e:
            logger.exception(f"[GateX Publish | {report_id}] Unexpected error: {e}")
            _audit(f"Unexpected error during publish: {e}")
            if pub_record:
                pub_record.publish_status = "publish_failed"
                pub_record.errors = f"Unexpected error: {e}"
                pub_record.publish_duration_ms = _elapsed_ms()
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()
            return PublishResult(
                success=False,
                report_id=report_id,
                publish_status="publish_failed",
                error=f"Unexpected error: {str(e)}",
                duration_ms=_elapsed_ms(),
                audit_trail=audit_trail,
            )

    # ------------------------------------------------------------------
    # Unpublish pipeline
    # ------------------------------------------------------------------
    async def unpublish(
        self,
        db: AsyncSession,
        report: dict,
        report_id: str,
        actor_id: str,
    ) -> Dict[str, Any]:
        """
        Attempts to unpublish a report from GateX.
        Since GateX does not document an official unpublish endpoint, this:
          1. Calls the abstraction method (always returns supported=False)
          2. Updates internal publication record to 'unpublished'
          3. Updates MOCK_REPORTS status
          4. Records audit log
          5. Returns the result with a clear message about manual removal
        """
        logger.info(f"Unpublish requested for report_id={report_id} by actor={actor_id}")

        # Find the active publication record
        try:
            doc_uuid = uuid.UUID(report_id)
        except ValueError:
            import hashlib
            m = hashlib.md5()
            m.update(report_id.encode("utf-8"))
            doc_uuid = uuid.UUID(m.hexdigest())

        stmt = (
            select(GateXPublication)
            .where(GateXPublication.document_id == doc_uuid)
            .where(GateXPublication.publish_status.in_(["published", "external_sync_pending"]))
        )
        result = await db.execute(stmt)
        pub_record = result.scalars().first()

        external_id = pub_record.external_report_id if pub_record else None

        # Call unpublish API (only if there is an external ID)
        if external_id:
            unpublish_result = await gatex_client.unpublish_report(external_id)
            if not unpublish_result.success:
                raise GateXError(f"GateX unpublish failed: {unpublish_result.error_message}")
            api_supported = True
            msg = unpublish_result.message
        else:
            api_supported = False
            msg = "No external ID found. Marked unpublished locally."

        # Update internal records
        if pub_record:
            pub_record.publish_status = "unpublished"
            pub_record.last_synced_at = datetime.now(timezone.utc)

        # Update MOCK_REPORTS
        from app.api.v1.endpoints.reports import MOCK_REPORTS
        if report_id in MOCK_REPORTS:
            MOCK_REPORTS[report_id]["status"] = "Rejected"
            MOCK_REPORTS[report_id]["publishReady"] = False
            MOCK_REPORTS[report_id]["publishStatus"] = "unpublished"
            MOCK_REPORTS[report_id]["externalReportId"] = None

        # Audit
        try:
            actor_uuid = uuid.UUID(actor_id)
        except (ValueError, TypeError):
            actor_uuid = None

        if pub_record:
            await audit_service.log_action(
                db=db,
                table_name="gatex_publications",
                record_id=pub_record.id,
                action="unpublish_requested",
                old_data={"publish_status": "published"},
                new_data={"publish_status": "unpublished", "external_api_supported": api_supported},
                changed_by=actor_uuid,
            )

        await db.commit()

        return {
            "report_id": report_id,
            "external_report_id": external_id,
            "internal_status": "unpublished",
            "external_api_supported": api_supported,
            "message": msg,
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
publish_orchestrator = PublishOrchestrator()
