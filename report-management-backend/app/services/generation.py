import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.workflow import GenerationJob
from app.models.enums import JobStatusType

class WorkerInterface:
    async def dispatch(self, job: GenerationJob) -> bool:
        raise NotImplementedError
    
    async def cancel(self, job: GenerationJob) -> bool:
        raise NotImplementedError

class GitHubActionsWorker(WorkerInterface):
    async def dispatch(self, job: GenerationJob) -> bool:
        # Mock triggering GitHub Action via webhook/API
        print(f"Triggering GitHub Action for Job {job.id}")
        return True

    async def cancel(self, job: GenerationJob) -> bool:
        print(f"Cancelling GitHub Action for Job {job.id}")
        return True

class LocalPythonWorker(WorkerInterface):
    async def dispatch(self, job: GenerationJob) -> bool:
        # Mock dispatching to Celery or background task
        print(f"Dispatching to Local Python Worker for Job {job.id}")
        return True

    async def cancel(self, job: GenerationJob) -> bool:
        print(f"Cancelling Local Python Worker for Job {job.id}")
        return True

class GenerationService:
    def __init__(self):
        # We can make this configurable via environment variables
        self.worker = GitHubActionsWorker()

    async def create_job(
        self, 
        db: AsyncSession, 
        document_id: uuid.UUID,
        topic: str,
        prompt: str,
        report_type: str,
        created_by: uuid.UUID
    ) -> GenerationJob:
        job = GenerationJob(
            id=uuid.uuid4(),
            document_id=document_id,
            topic=topic,
            prompt=prompt,
            report_type=report_type,
            created_by=created_by,
            status=JobStatusType.pending,
            started=datetime.now(timezone.utc)
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        
        # Dispatch to execution backend
        success = await self.worker.dispatch(job)
        if not success:
            job.status = JobStatusType.failed
            job.errors = "Failed to dispatch to worker"
            await db.commit()
            
        return job

    async def get_job(self, db: AsyncSession, job_id: uuid.UUID) -> Optional[GenerationJob]:
        return await db.get(GenerationJob, job_id)

    async def list_jobs(self, db: AsyncSession, limit: int = 100, offset: int = 0) -> List[GenerationJob]:
        stmt = select(GenerationJob).order_by(GenerationJob.started.desc()).limit(limit).offset(offset)
        result = await db.execute(stmt)
        return result.scalars().all()

    async def retry_job(self, db: AsyncSession, job_id: uuid.UUID) -> GenerationJob:
        job = await self.get_job(db, job_id)
        if not job:
            raise ValueError("Job not found")
        
        job.status = JobStatusType.pending
        job.retry_count += 1
        job.errors = None
        await db.commit()
        
        await self.worker.dispatch(job)
        return job

    async def cancel_job(self, db: AsyncSession, job_id: uuid.UUID) -> GenerationJob:
        job = await self.get_job(db, job_id)
        if not job:
            raise ValueError("Job not found")
            
        job.status = JobStatusType.cancelled
        await db.commit()
        
        await self.worker.cancel(job)
        return job

generation_service = GenerationService()
