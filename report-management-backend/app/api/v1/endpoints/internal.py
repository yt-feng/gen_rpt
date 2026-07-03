from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel, Field
from uuid import UUID
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.responses import APIResponse, success_response
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


async def _mark_job_completed_by_slug(db: AsyncSession, slug: str):
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
    import hashlib, uuid as _uuid

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
        logger.warning(f"[webhook] Could not find Document with slug='{slug}'")
        return {"status": "document_not_found", "slug": slug}

    # Find latest running/pending job for this document
    stmt = (
        select(GenerationJob)
        .where(GenerationJob.document_id == doc.id)
        .order_by(GenerationJob.started.desc())
        .limit(1)
    )
    job_result = await db.execute(stmt)
    job = job_result.scalar_one_or_none()

    if job and job.status in (JobStatusType.running, JobStatusType.pending):
        job.status = JobStatusType.completed
        job.completed = datetime.now(timezone.utc)
        await db.commit()
        logger.info(f"[webhook] Job {job.id} marked completed via webhook for slug={slug}")

    # Load payload from R2 and inject into MOCK_REPORTS
    payload = await _load_report_payload_from_r2(slug, doc.title or slug)
    title = doc.title or slug
    entry = _build_mock_report_entry(slug, title, slug, payload)
    MOCK_REPORTS[slug] = entry
    MOCK_REPORTS[str(doc.id)] = entry
    logger.info(f"[webhook] MOCK_REPORTS injected for slug={slug}")

    return {"status": "completed", "slug": slug, "job_id": str(job.id) if job else None}


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

    result = await _mark_job_completed_by_slug(db, slug)
    return success_response(data=result, message="Report generation event processed")


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
        logger.warning(f"[webhook] review-generated: Could not find Document with slug='{slug}'")
        return error_response(message=f"Could not find Document with slug='{slug}'")

    title = doc.title or slug
    # Fetch latest payload from R2 (which now includes the reviews/review.json)
    r2_payload = await _load_report_payload_from_r2(slug, title)
    entry = _build_mock_report_entry(slug, title, slug, r2_payload)
    
    # Update mock cache
    MOCK_REPORTS[slug] = entry
    MOCK_REPORTS[str(doc.id)] = entry
    logger.info(f"[webhook] MOCK_REPORTS updated with AI Review for slug={slug}")

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

