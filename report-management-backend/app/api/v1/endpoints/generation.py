from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from pydantic import BaseModel
from typing import List, Optional

from app.api.deps import get_db, get_current_user_placeholder, PageParams
from app.core.responses import APIResponse, success_response, error_response
from app.core.rate_limit import limiter
from app.services.generation import generation_service
from app.services.document import document_service
from app.schemas.document import DocumentCreate
from app.models.enums import JobStatusType
from app.core.config import settings
import re

router = APIRouter()

def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:60] or "web-research-topic"

class CreateJobRequest(BaseModel):
    document_id: Optional[UUID] = None
    topic: str
    prompt: Optional[str] = None
    industry: Optional[str] = None
    report_type: str = "technical"
    collection_ids: Optional[List[UUID]] = None
    rag_required: bool = False


def _require_validated_evidence(
    required: bool,
    validated_chunks: list,
    has_active_collections: bool = False,
) -> None:
    """
    Only hard-block with 422 when ALL THREE conditions are true:
      1. caller explicitly set rag_required=True
      2. the user actually has active knowledge collections (they intended RAG)
      3. retrieval still returned no validated chunks (retrieval genuinely failed)

    If the user has NO collections at all, allow the dispatch to proceed with
    web-search fallback — do not penalise users who haven't uploaded documents yet.
    """
    if required and has_active_collections and not validated_chunks:
        raise HTTPException(
            status_code=422,
            detail=(
                "RAG generation requires validated evidence, but no matching evidence "
                "was found in your knowledge collections. Upload and process relevant "
                "documents, then try again."
            ),
        )


@router.post("/jobs", response_model=APIResponse[dict])
@router.post("/", response_model=APIResponse[dict])
@limiter.limit("20/minute")
async def create_job(
    request: Request,
    req: CreateJobRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Initiate a new report generation job.
    """
    doc_id = req.document_id
    if not doc_id:
        import uuid
        unique_slug = f"{slugify(req.topic)}-{uuid.uuid4().hex[:6]}"
        doc_in = DocumentCreate(
            title=req.topic,
            slug=unique_slug,
            industry=req.industry,
            language="en"
        )
        doc = await document_service.create_document(
            db=db,
            doc_in=doc_in,
            user_id=UUID(user["id"])
        )
        doc_id = doc.id

    prompt = req.prompt or req.topic

    # Get document slug for context cache key mapping
    from app.models.document import Document
    doc_obj = await db.get(Document, doc_id)
    slug_val = doc_obj.slug if doc_obj else f"doc-{str(doc_id)[:8]}"

    rag_state = {"requested": bool(settings.RAG_ENABLED), "status": "disabled", "chunk_count": 0}
    if settings.RAG_ENABLED:
        from app.services.rag_integration import generation_context_service, RAGContextPreparationError
        from app.logging.logger import logger
        try:
            # Auto-resolve collection_ids: use provided ones or fall back to user's own collections
            effective_collection_ids = req.collection_ids
            if not effective_collection_ids:
                from app.models.knowledge import KnowledgeCollection
                from sqlalchemy import select as sa_select
                col_stmt = sa_select(KnowledgeCollection.id).where(
                    KnowledgeCollection.owner_id == UUID(user["id"]),
                    KnowledgeCollection.status == "active"
                )
                col_result = await db.execute(col_stmt)
                effective_collection_ids = col_result.scalars().all() or None
                if effective_collection_ids:
                    logger.info(f"RAG: Auto-resolved {len(effective_collection_ids)} collection(s) for user {user['id']}")

            # Prepare context (retrieval + validation + snapshotting + caching)
            context_package = await generation_context_service.prepare_context(
                db=db,
                query=prompt,
                collection_ids=effective_collection_ids,
                user_id=UUID(user["id"]),
                user_org_id=None,
                slug=slug_val
            )
            validated_chunks = context_package.get("validated_chunks", [])
            _require_validated_evidence(
                req.rag_required,
                validated_chunks,
                has_active_collections=bool(effective_collection_ids),
            )
            rag_state = {
                "requested": True,
                "status": (
                    context_package.get("context_metadata", {}).get("rag_status", "ready")
                    if validated_chunks else "no_matching_evidence"
                ),
                "chunk_count": len(validated_chunks),
                "estimated_tokens": context_package.get("context_metadata", {}).get("estimated_tokens", 0),
                "collection_ids": [str(cid) for cid in (effective_collection_ids or [])],
            }
            logger.info(f"RAG context pre-warmed for slug={slug_val}, cache_key=context:slug:{slug_val}")
        except Exception as e:
            logger.exception(f"Failed to pre-warm RAG context for slug={slug_val}: {e}")
            await db.rollback()
            if isinstance(e, HTTPException):
                if e.status_code == 422:
                    # A 422 from prepare_context means "no matching evidence".
                    # Degrade gracefully to web-search fallback instead of blocking
                    # the dispatch — the GitHub Actions workflow will handle the rest.
                    logger.warning(
                        f"RAG prepare_context returned 422 for slug={slug_val}; "
                        f"falling back to web-only mode. Detail: {e.detail}"
                    )
                    rag_state = {
                        "requested": True,
                        "status": "fallback_to_web",
                        "chunk_count": 0,
                    }
                    # fall through — dispatch proceeds normally
                else:
                    raise  # re-raise non-422 HTTP errors (401, 403, 503 …)
            else:
                stage = e.stage if isinstance(e, RAGContextPreparationError) else "unknown"
                raise HTTPException(
                    status_code=503,
                    detail=f"RAG context preparation failed during {stage}. No report was dispatched.",
                ) from e


    job = await generation_service.create_job(
        db=db,
        document_id=doc_id,
        topic=req.topic,
        prompt=prompt,
        report_type=req.report_type,
        created_by=UUID(user["id"]),
        rag_required=req.rag_required,
    )
    job.audit_metadata = {**(job.audit_metadata or {}), "rag": rag_state}
    await db.commit()

    from app.core.metrics import rag_generation_requests_total
    rag_generation_requests_total.labels(rag_enabled="true" if settings.RAG_ENABLED else "false").inc()

    return success_response(data={"job_id": str(job.id), "status": job.status.value}, message="Job created and dispatched")

@router.get("/jobs", response_model=APIResponse[list])
@router.get("/", response_model=APIResponse[list])
async def list_jobs(
    page: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    List generation jobs.
    """
    from app.models.document import Document
    from app.models.workflow import GenerationJob
    from sqlalchemy import select
    
    stmt = (
        select(GenerationJob, Document.industry)
        .join(Document, GenerationJob.document_id == Document.id)
        .order_by(GenerationJob.started.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    result = await db.execute(stmt)
    
    data = []
    for row in result.all():
        job = row[0]
        industry = row[1]
        data.append({
            "id": str(job.id),
            "topic": job.topic,
            "industry": industry or "Unknown",
            "status": job.status.value,
            "createdAt": job.started.isoformat() if job.started else None,
            "reportId": str(job.document_id)
        })
    return success_response(data=data, message="Fetched jobs")


# ===========================================================================
# RAG INTEGRATION ENDPOINTS (PHASE R9)
# ===========================================================================

@router.get("/preview-context", response_model=APIResponse[dict])
async def preview_knowledge_context(
    query: str,
    collection_ids: Optional[List[str]] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Preview the validated context package for a query.
    """
    from app.services.rag_integration import generation_context_service
    uuid_ids = [UUID(cid) for cid in collection_ids] if collection_ids else None
    pkg = await generation_context_service.prepare_context(
        db=db,
        query=query,
        collection_ids=uuid_ids,
        user_id=UUID(user["id"])
    )
    return success_response(data=pkg, message="Previewed knowledge context package")


@router.get("/snapshots/{snapshot_id}", response_model=APIResponse[dict])
async def get_knowledge_snapshot(
    snapshot_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Retrieve knowledge snapshot metadata.
    """
    from app.services.rag_integration import knowledge_snapshot_service
    snapshot = await knowledge_snapshot_service.get_snapshot(db, snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    
    data = {
        "id": str(snapshot.id),
        "knowledge_version": snapshot.knowledge_version,
        "collections_used": snapshot.collections_used,
        "documents_used": snapshot.documents_used,
        "chunks_used": snapshot.chunks_used,
        "embedding_version": snapshot.embedding_version,
        "validation_version": snapshot.validation_version,
        "retrieval_session_id": str(snapshot.retrieval_session_id) if snapshot.retrieval_session_id else None,
        "configuration": snapshot.configuration,
        "r2_path": snapshot.r2_path,
        "created_at": snapshot.created_at.isoformat()
    }
    return success_response(data=data, message="Fetched knowledge snapshot")


@router.get("/sessions/{session_id}", response_model=APIResponse[dict])
async def get_generation_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Retrieve retrieval/validation session details using the ValidationReport.
    """
    from app.models.validation import ValidationReport
    from sqlalchemy import select
    stmt = select(ValidationReport).where(ValidationReport.session_id == session_id).order_by(ValidationReport.created_at.desc()).limit(1)
    res = await db.execute(stmt)
    report = res.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Validation report/session not found")
        
    data = {
        "id": str(report.id),
        "session_id": str(report.session_id) if report.session_id else None,
        "validation_summary": report.validation_summary,
        "confidence_scores": report.confidence_scores,
        "authority_scores": report.authority_scores,
        "freshness_scores": report.freshness_scores,
        "conflicts": report.conflicts,
        "duplicate_analysis": report.duplicate_analysis,
        "evidence_completeness": report.evidence_completeness,
        "unsupported_evidence": report.unsupported_evidence,
        "recommendations": report.recommendations,
        "created_at": report.created_at.isoformat()
    }
    return success_response(data=data, message="Fetched generation retrieval validation session")


@router.get("/jobs/{job_id}/attribution", response_model=APIResponse[list])
async def get_evidence_attribution(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Retrieve evidence attribution entries for a specific generation job.
    """
    from app.services.rag_integration import evidence_attribution_service
    attributions = await evidence_attribution_service.get_attributions_for_job(db, job_id)
    
    data = []
    for attr in attributions:
        data.append({
            "id": str(attr.id),
            "generation_job_id": str(attr.generation_job_id) if attr.generation_job_id else None,
            "section_id": attr.section_id,
            "supporting_chunks": attr.supporting_chunks,
            "supporting_documents": attr.supporting_documents,
            "supporting_sources": attr.supporting_sources,
            "supporting_collections": attr.supporting_collections,
            "confidence": attr.confidence,
            "validation_report_id": str(attr.validation_report_id) if attr.validation_report_id else None,
            "snapshot_id": str(attr.snapshot_id) if attr.snapshot_id else None,
            "created_at": attr.created_at.isoformat()
        })
    return success_response(data=data, message="Fetched evidence attributions")


@router.get("/analytics", response_model=APIResponse[dict])
async def get_generation_analytics(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Fetch global report generation context and LLM analytics metrics.
    """
    from app.services.rag_integration import generation_analytics_service
    summary = await generation_analytics_service.get_analytics_summary(db)
    return success_response(data=summary, message="Fetched generation analytics")


@router.get("/{slug}/context", response_model=APIResponse[dict])
async def get_slug_context(
    slug: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Get cached RAG Context Package for a report generation slug.
    Used by workers to retrieve pre-validated facts.
    """
    from app.services.rag_integration import context_cache_service
    pkg = await context_cache_service.get_cached_context(db, f"context:slug:{slug}")
    if not pkg:
        return success_response(
            data={"validated_chunks": [], "validated_sources": [], "document_references": []},
            message="No context found, fallback empty context provided"
        )
    return success_response(data=pkg, message="Fetched cached context package")


@router.get("/{job_id}", response_model=APIResponse[dict])
async def get_job_status(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Get the status of a specific generation job.
    """
    job = await generation_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    return success_response(data={
        "job_id": str(job.id), 
        "status": job.status.value,
        "logs": job.logs,
        "errors": job.errors,
        "duration": job.duration
    }, message="Fetched job status")

@router.post("/{job_id}/retry", response_model=APIResponse[dict])
async def retry_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Retry a failed or cancelled generation job.
    """
    try:
        job = await generation_service.retry_job(db, job_id)
        return success_response(data={"job_id": str(job.id), "status": job.status.value}, message="Job retried")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{job_id}/cancel", response_model=APIResponse[dict])
async def cancel_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Cancel an running generation job.
    """
    try:
        job = await generation_service.cancel_job(db, job_id)
        return success_response(data={"job_id": str(job.id), "status": job.status.value}, message="Job cancelled")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{job_id}/logs", response_model=APIResponse[dict])
async def get_job_logs(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Get the logs for a specific generation job.
    """
    job = await generation_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    return success_response(data={"logs": job.logs or ""}, message="Fetched job logs")


# ---------------------------------------------------------------------------
# BULK GENERATION ENDPOINTS
# ---------------------------------------------------------------------------

MAX_CONCURRENT_BULK = 20   # Hard limit — matches GitHub Actions free-plan cap
DISPATCH_STAGGER_SEC = 2   # Seconds between each GHA dispatch call

class BulkJobItem(BaseModel):
    topic: str
    industry: Optional[str] = None

class BulkCreateRequest(BaseModel):
    jobs: List[BulkJobItem]
    limit: Optional[int] = None
    collection_ids: Optional[List[UUID]] = None

@router.post("/bulk", response_model=APIResponse[dict])
async def create_bulk_jobs(
    req: BulkCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Submit a batch of report generation jobs from a CSV upload.

    - Accepts up to MAX_CONCURRENT_BULK (20) items per call.
    - Each item creates a Document + GenerationJob row in the DB.
    - Jobs are dispatched to generate_deep_research_bulk.yml via the GitHub API
      with a 2-second stagger to avoid rate limits.
    - Returns immediately with the list of created job IDs.
    """
    import uuid as _uuid
    import asyncio

    if not req.jobs:
        return error_response(message="No jobs provided")

    # 1. Fetch queue pause state and concurrency threshold limit from R2
    is_paused = False
    limit_val = MAX_CONCURRENT_BULK
    from app.storage.provider import storage_provider
    import json
    try:
        data_bytes = await storage_provider.download("catalog/bulk_queue_state.json")
        if data_bytes:
            state = json.loads(data_bytes.decode("utf-8"))
            is_paused = state.get("paused", False)
            limit_val = state.get("limit", MAX_CONCURRENT_BULK)
    except Exception:
        pass

    # If the request contains a new limit, update the persistent queue state
    if req.limit is not None and req.limit != limit_val:
        limit_val = req.limit
        try:
            state_data = json.dumps({"paused": is_paused, "limit": limit_val})
            await storage_provider.upload(
                file_data=state_data.encode("utf-8"),
                path="catalog/bulk_queue_state.json",
                content_type="application/json"
            )
        except Exception:
            pass

    # 2. Query currently running bulk jobs in DB
    from app.models.workflow import GenerationJob
    from app.models.enums import JobStatusType
    from sqlalchemy import select, func

    stmt = select(func.count(GenerationJob.id)).where(
        GenerationJob.report_type == "bulk",
        GenerationJob.status == JobStatusType.running
    )
    res = await db.execute(stmt)
    running_count = res.scalar() or 0

    slots_available = max(0, limit_val - running_count)
    if is_paused:
        slots_available = 0

    created = []
    errors = []
    dispatched_count = 0
    queued_count = 0

    effective_collection_ids = req.collection_ids
    if settings.RAG_ENABLED and not effective_collection_ids:
        from app.models.knowledge import KnowledgeCollection
        col_stmt = select(KnowledgeCollection.id).where(
            KnowledgeCollection.owner_id == UUID(user["id"]),
            KnowledgeCollection.status == "active",
        )
        col_result = await db.execute(col_stmt)
        effective_collection_ids = list(col_result.scalars().all()) or None

    for item in req.jobs:
        try:
            topic = item.topic.strip()
            industry = (item.industry or "").strip() or None
            if not topic:
                errors.append({"topic": item.topic, "error": "Empty topic"})
                continue

            unique_slug = f"{slugify(topic)}-{_uuid.uuid4().hex[:6]}"
            doc_in = DocumentCreate(
                title=topic,
                slug=unique_slug,
                industry=industry,
                language="en"
            )
            doc = await document_service.create_document(
                db=db,
                doc_in=doc_in,
                user_id=UUID(user["id"])
            )

            rag_state = {"requested": bool(settings.RAG_ENABLED), "status": "disabled", "chunk_count": 0}
            if settings.RAG_ENABLED:
                from app.services.rag_integration import generation_context_service
                try:
                    context_package = await generation_context_service.prepare_context(
                        db=db,
                        query=topic,
                        collection_ids=effective_collection_ids,
                        user_id=UUID(user["id"]),
                        user_org_id=None,
                        slug=unique_slug,
                    )
                    validated_chunks = context_package.get("validated_chunks", [])
                    if not validated_chunks:
                        errors.append({
                            "topic": topic,
                            "error": "No validated RAG evidence found; report was not dispatched.",
                        })
                        continue
                    rag_state = {
                        "requested": True,
                        "status": context_package.get("context_metadata", {}).get("rag_status", "ready"),
                        "chunk_count": len(validated_chunks),
                        "estimated_tokens": context_package.get("context_metadata", {}).get("estimated_tokens", 0),
                        "collection_ids": [str(cid) for cid in (effective_collection_ids or [])],
                    }
                except Exception as exc:
                    await db.rollback()
                    errors.append({
                        "topic": topic,
                        "error": f"RAG context preparation failed; report was not dispatched: {exc}",
                    })
                    continue

            # Determine whether to dispatch immediately or keep in queue
            should_dispatch = slots_available > 0
            
            job = await generation_service.create_bulk_job(
                db=db,
                document_id=doc.id,
                topic=topic,
                slug=unique_slug,
                industry=industry,
                created_by=UUID(user["id"]),
                dispatch=should_dispatch
            )
            job.audit_metadata = {**(job.audit_metadata or {}), "rag": rag_state}
            await db.commit()

            status_val = job.status.value
            if should_dispatch:
                slots_available -= 1
                dispatched_count += 1
                # Stagger dispatches to respect GitHub API rate limits
                await asyncio.sleep(DISPATCH_STAGGER_SEC)
            else:
                queued_count += 1
                # If not dispatched, keep status as pending
                status_val = "pending"

            created.append({
                "job_id": str(job.id),
                "slug": unique_slug,
                "topic": topic,
                "industry": industry,
                "status": status_val,
                "rag": rag_state,
            })

        except Exception as e:
            errors.append({"topic": item.topic, "error": str(e)})

    return success_response(
        data={
            "dispatched": dispatched_count,
            "queued": queued_count,
            "errors": errors,
            "jobs": created,
        },
        message=f"Submitted {len(created)} jobs. Dispatched {dispatched_count}, queued {queued_count}."
    )


@router.get("/bulk/queue", response_model=APIResponse[list])
async def get_bulk_queue(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Return live status of all bulk generation jobs, ordered newest first.
    Used by the frontend queue monitor to poll for updates every 5 seconds.
    """
    from app.models.document import Document
    from app.models.workflow import GenerationJob
    from sqlalchemy import select

    stmt = (
        select(GenerationJob, Document)
        .join(Document, GenerationJob.document_id == Document.id)
        .where(GenerationJob.report_type == "bulk")
        .order_by(GenerationJob.started.desc())
        .limit(200)
    )
    result = await db.execute(stmt)

    data = []
    for job, doc in result.all():
        data.append({
            "job_id": str(job.id),
            "topic": job.topic,
            "industry": doc.industry or "",
            "slug": doc.slug or str(job.document_id),
            "status": job.status.value,
            "startedAt": job.started.isoformat() if job.started else None,
            "completedAt": job.completed.isoformat() if getattr(job, "completed", None) else None,
            "errors": job.errors,
        })
    return success_response(data=data, message="Fetched bulk queue")


class QueueStateUpdate(BaseModel):
    paused: Optional[bool] = None
    limit: Optional[int] = None


@router.get("/bulk/queue-state", response_model=APIResponse[dict])
async def get_bulk_queue_state(
    user: dict = Depends(get_current_user_placeholder)
):
    from app.storage.provider import storage_provider
    import json
    is_paused = False
    limit_val = 20
    try:
        data_bytes = await storage_provider.download("catalog/bulk_queue_state.json")
        if data_bytes:
            state = json.loads(data_bytes.decode("utf-8"))
            is_paused = state.get("paused", False)
            limit_val = state.get("limit", 20)
    except Exception:
        pass
    return success_response(data={"paused": is_paused, "limit": limit_val}, message="Fetched queue state")


@router.post("/bulk/queue-state", response_model=APIResponse[dict])
async def update_bulk_queue_state(
    req: QueueStateUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    from app.storage.provider import storage_provider
    from app.services.generation import generation_service
    import json
    try:
        # Load existing state to preserve parameters
        is_paused = False
        limit_val = 20
        try:
            data_bytes = await storage_provider.download("catalog/bulk_queue_state.json")
            if data_bytes:
                state = json.loads(data_bytes.decode("utf-8"))
                is_paused = state.get("paused", False)
                limit_val = state.get("limit", 20)
        except Exception:
            pass

        # Update values
        if req.paused is not None:
            is_paused = req.paused
        if req.limit is not None:
            limit_val = req.limit

        state_data = json.dumps({"paused": is_paused, "limit": limit_val})
        await storage_provider.upload(
            file_data=state_data.encode("utf-8"),
            path="catalog/bulk_queue_state.json",
            content_type="application/json"
        )
        if not is_paused:
            await generation_service.process_bulk_queue(db)
        return success_response(data={"paused": is_paused, "limit": limit_val}, message="Queue state updated")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update queue state: {e}")


@router.post("/bulk/clear-queue", response_model=APIResponse[dict])
async def clear_bulk_queue(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    from app.models.workflow import GenerationJob
    from app.models.enums import JobStatusType
    from sqlalchemy import update
    try:
        stmt = (
            update(GenerationJob)
            .where(
                GenerationJob.report_type == "bulk",
                GenerationJob.status == JobStatusType.pending
            )
            .values(
                status=JobStatusType.failed,
                errors="Job cancelled by user via queue clearing"
            )
        )
        res = await db.execute(stmt)
        await db.commit()
        # Note: res.rowcount represents the number of cleared rows
        cleared = res.rowcount if hasattr(res, "rowcount") else 0
        return success_response(data={"cleared_count": cleared}, message="Bulk pending queue cleared successfully")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear pending queue: {e}")


@router.post("/bulk/cancel-all", response_model=APIResponse[dict])
async def cancel_all_bulk_jobs(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    from app.models.workflow import GenerationJob
    from app.models.enums import JobStatusType
    from sqlalchemy import update
    import httpx
    from app.core.config import settings

    try:
        # 1. Update all pending and running bulk jobs to failed in the database
        stmt = (
            update(GenerationJob)
            .where(
                GenerationJob.report_type == "bulk",
                GenerationJob.status.in_([JobStatusType.pending, JobStatusType.running])
            )
            .values(
                status=JobStatusType.failed,
                errors="Job manually cancelled via Cancel All workflows"
            )
        )
        res = await db.execute(stmt)
        await db.commit()
        cleared_count = res.rowcount if hasattr(res, "rowcount") else 0

        # 2. Call GitHub API to cancel all active runs
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Antigravity-Agent"
        }
        
        runs = []
        async with httpx.AsyncClient() as client:
            for status in ["queued", "in_progress"]:
                url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/actions/runs?status={status}"
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    runs.extend(data.get("workflow_runs", []))

            # Cancel each active run
            cancelled_runs_count = 0
            for run in runs:
                run_id = run.get("id")
                if run_id:
                    cancel_url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/actions/runs/{run_id}/cancel"
                    cancel_resp = await client.post(cancel_url, headers=headers)
                    if cancel_resp.status_code in [202, 204, 200]:
                        cancelled_runs_count += 1

        return success_response(
            data={"cleared_jobs": cleared_count, "cancelled_github_runs": cancelled_runs_count},
            message="Successfully cancelled all bulk jobs and active workflows"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel workflows: {e}")




