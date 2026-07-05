from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from pydantic import BaseModel
from typing import List, Optional

from app.api.deps import get_db, get_current_user_placeholder, PageParams
from app.core.responses import APIResponse, success_response, error_response
from app.services.generation import generation_service
from app.services.document import document_service
from app.schemas.document import DocumentCreate
from app.models.enums import JobStatusType
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

@router.post("/jobs", response_model=APIResponse[dict])
@router.post("/", response_model=APIResponse[dict])
async def create_job(
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

    job = await generation_service.create_job(
        db=db,
        document_id=doc_id,
        topic=req.topic,
        prompt=prompt,
        report_type=req.report_type,
        created_by=UUID(user["id"])
    )
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

    # 1. Fetch queue pause state from R2
    is_paused = False
    try:
        from app.storage.provider import storage_provider
        import json
        obj = await storage_provider.get("catalog/bulk_queue_state.json")
        if obj:
            state = json.loads(obj.decode("utf-8") if isinstance(obj, bytes) else obj.text())
            is_paused = state.get("paused", False)
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

    slots_available = max(0, MAX_CONCURRENT_BULK - running_count)
    if is_paused:
        slots_available = 0

    created = []
    errors = []
    dispatched_count = 0
    queued_count = 0

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
    paused: bool


@router.get("/bulk/queue-state", response_model=APIResponse[dict])
async def get_bulk_queue_state(
    user: dict = Depends(get_current_user_placeholder)
):
    from app.storage.provider import storage_provider
    import json
    is_paused = False
    try:
        obj = await storage_provider.get("catalog/bulk_queue_state.json")
        if obj:
            state = json.loads(obj.decode("utf-8") if isinstance(obj, bytes) else obj.text())
            is_paused = state.get("paused", False)
    except Exception:
        pass
    return success_response(data={"paused": is_paused}, message="Fetched queue state")


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
        state_data = json.dumps({"paused": req.paused})
        await storage_provider.put(
            "catalog/bulk_queue_state.json",
            state_data.encode("utf-8"),
            options={"httpMetadata": {"contentType": "application/json"}}
        )
        if not req.paused:
            await generation_service.process_bulk_queue(db)
        return success_response(data={"paused": req.paused}, message="Queue state updated")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update queue state: {e}")
