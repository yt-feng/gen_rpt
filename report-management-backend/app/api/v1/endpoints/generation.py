from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from pydantic import BaseModel
from typing import List, Optional

from app.api.deps import get_db, get_current_user_placeholder, PageParams
from app.core.responses import APIResponse, success_response, error_response
from app.services.generation import generation_service
from app.models.enums import JobStatusType

router = APIRouter()

class CreateJobRequest(BaseModel):
    document_id: UUID
    topic: str
    prompt: str
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
    job = await generation_service.create_job(
        db=db,
        document_id=req.document_id,
        topic=req.topic,
        prompt=req.prompt,
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
    jobs = await generation_service.list_jobs(db, limit=page.limit, offset=page.offset)
    data = [{"job_id": str(j.id), "status": j.status.value, "topic": j.topic, "started": j.started} for j in jobs]
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
