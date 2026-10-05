from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel, Field
from uuid import UUID
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.responses import APIResponse, success_response, error_response
from app.api.deps import get_db
from app.services.workflow import workflow_service
from app.logging.logger import logger
from app.core.config import settings

router = APIRouter()


def verify_internal_token(x_internal_token: str = Header(...)):
    """
    Verifies internal requests from GitHub Actions workers.
    Reads INTERNAL_TOKEN from settings (env var). Falls back to
    'trusted-worker-secret' if not configured (local dev only).
    """
    expected = getattr(settings, "INTERNAL_TOKEN", None) or "trusted-worker-secret"
    if x_internal_token != expected:
        raise HTTPException(status_code=403, detail="Invalid internal token")


class WorkflowEventPayload(BaseModel):
    # Accept a plain string — may be a UUID or a slug string (sent by GitHub Actions)
    document_id: str = Field(description="Target document ID or slug")
    idempotency_key: str = Field(description="Unique key to prevent duplicate processing")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    actor_id: Optional[str] = None


@router.get("/generation-contract", response_model=APIResponse[dict], dependencies=[Depends(verify_internal_token)])
async def generation_contract():
    """Read-only capability negotiation for independently deployed workers."""
    return success_response(data={"generation_receipt_contract": "v1"}, message="Exact report receipt supported")


async def _mark_job_completed_by_slug(db: AsyncSession, slug: str, receipt=None):
    """
    Finds a GenerationJob by document slug and marks it completed + injects MOCK_REPORTS.
    Called by the report-generated webhook so the frontend immediately shows the report.
    """
    from app.models.workflow import GenerationJob
    from app.models.document import Document
    from app.models.enums import JobStatusType
    from app.services.generation import _load_report_payload_from_r2, _build_mock_report_entry
    from app.api.v1.endpoints.reports import MOCK_REPORTS
    from sqlalchemy import select
    from datetime import datetime, timezone
    import uuid as _uuid

    # Resolve document by slug
    doc_result = await db.execute(select(Document).where(Document.slug == slug))
    doc = doc_result.scalar_one_or_none()

    if not doc:
        # Try looking up by UUID if slug happens to be a UUID
        try:
            doc_uuid = _uuid.UUID(slug)
            doc = await db.get(Document, doc_uuid)
        except ValueError:
            pass

    if not doc:
        logger.warning(f"[webhook] Could not find Document with slug='{slug}', attempting to load from R2 anyway.")
        doc_id_str = slug
        title = slug.replace('-', ' ').title()
    else:
        doc_id_str = str(doc.id)
        title = doc.title or slug

    # A receipt names exact uploaded bytes; no historical/fuzzy report can
    # complete a new attempt. Legacy events must also load real content first.
    if receipt is not None:
        from app.services.generation_receipt import load_receipted_report
        from app.storage.provider import storage_provider
        try:
            payload = await load_receipted_report(slug, receipt, storage_provider)
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(status_code=409, detail="Generated report receipt is not ready or invalid") from exc
    else:
        payload = await _load_report_payload_from_r2(slug, title)
        if not isinstance(payload, dict) or not payload.get("sections"):
            raise HTTPException(status_code=409, detail="Generated report content is not available")

    job = None
    if receipt is not None and receipt.get("job_id"):
        try:
            job = await db.get(GenerationJob, _uuid.UUID(receipt["job_id"]))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Invalid generation job identity") from exc
        if (not job or not doc or job.document_id != doc.id
                or str(job.retry_count) != str(receipt.get("job_retry_count"))):
            raise HTTPException(status_code=409, detail="Generation receipt belongs to a different job attempt")
    elif receipt is None and doc:
        job_result = await db.execute(select(GenerationJob).where(
            GenerationJob.document_id == doc.id).order_by(GenerationJob.started.desc()).limit(1))
        job = job_result.scalar_one_or_none()
        if job and (job.audit_metadata or {}).get("receipt_expected"):
            raise HTTPException(status_code=409, detail="This job requires an exact generation receipt")
    # Manual/jobless workflows can publish a real report, but cannot claim a
    # previous backend job merely because its slug happens to match.
    if job:
        prior = (job.audit_metadata or {}).get("generation_outcome", {})
        if job.status == JobStatusType.completed and receipt is not None:
            if prior.get("payload_sha256") != receipt.get("payload_sha256"):
                raise HTTPException(status_code=409, detail="Completed job has a different generation receipt")
        elif job.status not in (JobStatusType.running, JobStatusType.pending):
            raise HTTPException(status_code=409, detail="Generation job is not awaiting this result")
        else:
            from app.services.generation import generation_service
            job.status = JobStatusType.completed
            job.completed = datetime.now(timezone.utc)
            if receipt is not None:
                job.audit_metadata = {**(job.audit_metadata or {}), "generation_outcome": receipt}
            await db.commit()
            await generation_service.process_bulk_queue(db)
    entry = _build_mock_report_entry(slug, title, slug, payload)
    entry["generationOutcome"] = payload.get("generation_outcome", {})
    MOCK_REPORTS[slug] = entry
    MOCK_REPORTS[doc_id_str] = entry
    return {"status": "completed", "slug": slug, "job_id": str(job.id) if job else None,
            "generation_outcome": payload.get("generation_outcome", {})}


async def _mark_job_failed_by_slug(db: AsyncSession, slug: str, error: str, attempt=None):
    """Release a failed generation job immediately instead of waiting for the R2 poller."""
    from app.models.workflow import GenerationJob
    from app.models.document import Document
    from app.models.enums import JobStatusType
    from sqlalchemy import select
    from datetime import datetime, timezone
    import uuid as _uuid

    doc_result = await db.execute(select(Document).where(Document.slug == slug))
    doc = doc_result.scalar_one_or_none()
    if not doc:
        try:
            doc = await db.get(Document, _uuid.UUID(slug))
        except ValueError:
            pass
    if not doc:
        return {"status": "document_not_found", "slug": slug, "job_id": None}

    if attempt is not None:
        if not attempt.get("job_id"):
            return {"status": "untracked_failure", "slug": slug, "job_id": None}
        try:
            job = await db.get(GenerationJob, _uuid.UUID(attempt["job_id"]))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Invalid failed job identity") from exc
        if not job or job.document_id != doc.id or str(job.retry_count) != str(attempt.get("job_retry_count")):
            raise HTTPException(status_code=409, detail="Failure belongs to a different generation attempt")
    else:
        job_result = await db.execute(
            select(GenerationJob).where(GenerationJob.document_id == doc.id)
            .order_by(GenerationJob.started.desc()).limit(1))
        job = job_result.scalar_one_or_none()
        if job and (job.audit_metadata or {}).get("receipt_expected"):
            raise HTTPException(status_code=409, detail="This failure requires its generation attempt identity")
    if job and job.status in (JobStatusType.running, JobStatusType.pending):
        from app.services.generation import generation_service

        job.status = JobStatusType.failed
        job.errors = error[:2000]
        job.completed = datetime.now(timezone.utc)
        await db.commit()
        await generation_service.process_bulk_queue(db)
        logger.info(f"[webhook] Job {job.id} marked failed for slug={slug}")

    return {"status": "failed", "slug": slug, "job_id": str(job.id) if job else None}


@router.post("/events/report-generated", response_model=APIResponse[dict], dependencies=[Depends(verify_internal_token)])
async def handle_report_generated(
    payload: WorkflowEventPayload,
    db: AsyncSession = Depends(get_db)
):
    """
    Webhook invoked by GitHub Actions when report generation completes.
    Directly marks the job as completed and injects the report into MOCK_REPORTS.
    """
    slug = payload.document_id
    logger.info(f"[webhook] report-generated received for document_id/slug: {slug}")

    result = await _mark_job_completed_by_slug(db, slug, (payload.metadata or {}).get("generation_receipt"))
    return success_response(data=result, message="Report generation event processed")


@router.post("/events/report-failed", response_model=APIResponse[dict], dependencies=[Depends(verify_internal_token)])
async def handle_report_failed(
    payload: WorkflowEventPayload,
    db: AsyncSession = Depends(get_db)
):
    """Webhook invoked when generation or grounded-quality validation fails."""
    slug = payload.document_id
    error = str((payload.metadata or {}).get("error") or "Report generation workflow failed")
    logger.warning(f"[webhook] report-failed received for document_id/slug: {slug}")
    result = await _mark_job_failed_by_slug(db, slug, error, (payload.metadata or {}).get("generation_attempt"))
    return success_response(data=result, message="Report failure event processed")


@router.post("/events/review-generated", response_model=APIResponse[dict], dependencies=[Depends(verify_internal_token)])
async def handle_review_generated(
    payload: WorkflowEventPayload,
    db: AsyncSession = Depends(get_db)
):
    """Webhook invoked by GitHub Actions when AI review generation completes."""
    slug = payload.document_id
    logger.info(f"[webhook] review-generated received for document_id/slug: {slug}")
    
    from app.services.generation import _load_report_payload_from_r2, _build_mock_report_entry
    from app.api.v1.endpoints.reports import MOCK_REPORTS
    from sqlalchemy import select
    from app.models.document import Document
    import uuid as _uuid
    
    # Resolve document by slug
    doc_result = await db.execute(select(Document).where(Document.slug == slug))
    doc = doc_result.scalar_one_or_none()
    
    if not doc:
        try:
            doc_uuid = _uuid.UUID(slug)
            doc = await db.get(Document, doc_uuid)
        except ValueError:
            pass
            
    if not doc:
        logger.warning(f"[webhook] review-generated: Could not find Document with slug='{slug}', updating cache anyway")
        doc_id_str = slug
        title = slug.replace('-', ' ').title()
    else:
        doc_id_str = str(doc.id)
        title = doc.title or slug

    # Fetch latest payload from R2 (which now includes the reviews/review.json)
    try:
        r2_payload = await _load_report_payload_from_r2(slug, title)
        entry = _build_mock_report_entry(slug, title, slug, r2_payload)
        
        # Update mock cache
        MOCK_REPORTS[slug] = entry
        MOCK_REPORTS[doc_id_str] = entry
        logger.info(f"[webhook] MOCK_REPORTS updated with AI Review for slug={slug}")
    except Exception as e:
        logger.error(f"[webhook] Failed to load payload from R2 for review update slug={slug}: {e}")
        return error_response(message=f"Could not load payload from R2 for slug='{slug}'")

    return success_response(data={"slug": slug}, message="Review generation event processed and cached")


@router.post("/events/upload-complete", response_model=APIResponse[dict], dependencies=[Depends(verify_internal_token)])
async def handle_upload_complete(
    payload: WorkflowEventPayload,
    db: AsyncSession = Depends(get_db)
):
    """Webhook invoked when artifacts are fully uploaded to R2."""
    logger.info(f"[webhook] upload-complete for: {payload.document_id}")
    return success_response(data={"slug": payload.document_id}, message="Upload complete event acknowledged")


@router.post("/events/publish-requested", response_model=APIResponse[dict], dependencies=[Depends(verify_internal_token)])
async def handle_publish_requested(
    payload: WorkflowEventPayload,
    db: AsyncSession = Depends(get_db)
):
    """Webhook invoked when a publish is requested."""
    logger.info(f"[webhook] publish-requested for: {payload.document_id}")
    return success_response(data={"slug": payload.document_id}, message="Publish requested event acknowledged")


class ImageRegeneratedPayload(BaseModel):
    document_id: str
    image_key: str
    prompt: str

@router.post("/events/image-regenerated", response_model=APIResponse[dict], dependencies=[Depends(verify_internal_token)])
async def handle_image_regenerated(
    payload: ImageRegeneratedPayload,
    db: AsyncSession = Depends(get_db)
):
    """
    Webhook invoked by GitHub Actions when image regeneration finishes.
    Refreshes the R2 presigned URL in MOCK_REPORTS cache.
    """
    from app.api.v1.endpoints.reports import MOCK_REPORTS
    from app.storage.provider import storage_provider
    
    slug = payload.document_id
    safe_key = payload.image_key.split("/")[-1]
    logger.info(f"[webhook] image-regenerated received for slug: {slug}, key: {safe_key}")

    # Resolve R2 path and generate fresh presigned URL
    report = MOCK_REPORTS.get(slug)
    if not report:
        logger.warning(f"[webhook] image-regenerated: report not found in MOCK_REPORTS cache for slug: {slug}")
        return success_response(data={}, message="Report not cached, skipping update")

    r2_prefix = report.get("r2_prefix") or f"reports/{slug}/"
    if r2_prefix and not r2_prefix.endswith("/"):
        r2_prefix += "/"
    
    r2_key = f"{r2_prefix}current/assets/{safe_key}"
    
    try:
        new_url = await storage_provider.get_signed_url(r2_key, expiration_sec=3600)
        
        # Update in-memory cache URL
        report_content = report.get("reportContent", {})
        for img in report_content.get("images", []):
            if img.get("key") == safe_key:
                img["url"] = new_url
                break
                
        logger.info(f"[webhook] image-regenerated: Successfully refreshed presigned URL for image_key='{safe_key}' in report slug='{slug}'")
    except Exception as e:
        logger.error(f"[webhook] Failed to get signed URL for regenerated image: {e}")
        
    return success_response(data={"slug": slug}, message="Image regeneration event processed")


def verify_internal_bearer_token(authorization: Optional[str] = Header(None), x_internal_token: Optional[str] = Header(None)):
    expected = getattr(settings, "INTERNAL_TOKEN", None) or "trusted-worker-secret"
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            if parts[1] == expected:
                return
    if x_internal_token == expected:
        return
    raise HTTPException(status_code=403, detail="Invalid internal token")


@router.get("/context/{slug}", response_model=APIResponse[dict], dependencies=[Depends(verify_internal_bearer_token)], include_in_schema=False)
async def get_internal_context(
    slug: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieve cached RAG context package for internal workers.
    Returns:
      - validated_chunks: raw chunk objects
      - context_text: pre-formatted string ready for prompt injection
      - has_rag_context: True if private document context is available
      - document_count: number of distinct documents in context
    """
    from app.services.rag_integration import context_cache_service
    pkg = await context_cache_service.get_cached_context(db, f"context:slug:{slug}")
    if not pkg:
        return success_response(
            data={
                "validated_chunks": [],
                "validated_sources": [],
                "document_references": [],
                "context_text": "",
                "has_rag_context": False,
                "document_count": 0,
                "context_status": "missing_or_expired",
                "recovery_action": "retry_original_job_with_scoped_context_preparation",
            },
            message="Context is missing or expired; prepare and validate the original job scope before dispatch"
        )

    # Build a clean, pre-formatted context_text for direct prompt injection
    chunks = pkg.get("validated_chunks", [])
    doc_references = pkg.get("document_references", [])

    # Group chunks by document for cleaner formatting
    doc_map: dict = {}
    for chunk in chunks:
        doc_id = str(chunk.get("document_id") or chunk.get("chunk_id", "unknown"))
        doc_title = chunk.get("document_title") or chunk.get("source") or "Document"
        if doc_id not in doc_map:
            doc_map[doc_id] = {"title": doc_title, "chunks": []}
        doc_map[doc_id]["chunks"].append(chunk.get("text", ""))

    context_parts = []
    for doc_id, doc_data in doc_map.items():
        title = doc_data["title"]
        body = "\n\n".join(doc_data["chunks"])
        context_parts.append(f"=== DOCUMENT: {title} ===\n{body}")

    context_text = "\n\n".join(context_parts)

    # Count distinct source documents
    seen_docs = set()
    for chunk in chunks:
        doc_id = chunk.get("document_id") or chunk.get("chunk_id", "")
        if doc_id:
            seen_docs.add(str(doc_id))
    document_count = len(seen_docs) or len(doc_references)

    enriched_pkg = {
        **pkg,
        "context_text": context_text,
        "has_rag_context": bool(chunks),
        "document_count": document_count,
    }
    return success_response(data=enriched_pkg, message="Fetched cached context package")
