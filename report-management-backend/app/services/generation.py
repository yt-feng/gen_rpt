import uuid
import asyncio
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
        print(f"Triggering GitHub Action for Job {job.id}")
        return True

    async def cancel(self, job: GenerationJob) -> bool:
        print(f"Cancelling GitHub Action for Job {job.id}")
        return True

class LocalPythonWorker(WorkerInterface):
    async def dispatch(self, job: GenerationJob) -> bool:
        print(f"Dispatching to Local Python Worker for Job {job.id}")
        return True

    async def cancel(self, job: GenerationJob) -> bool:
        print(f"Cancelling Local Python Worker for Job {job.id}")
        return True

async def simulate_job_execution(job_id: uuid.UUID):
    # Wait 3 seconds: pending -> running
    await asyncio.sleep(3)
    from app.database.session import async_session_maker
    async with async_session_maker() as session:
        job = await session.get(GenerationJob, job_id)
        if job and job.status == JobStatusType.pending:
            job.status = JobStatusType.running
            await session.commit()

    # Wait 5 seconds: running -> completed
    await asyncio.sleep(5)
    async with async_session_maker() as session:
        job = await session.get(GenerationJob, job_id)
        if job and job.status == JobStatusType.running:
            job.status = JobStatusType.completed
            job.completed = datetime.now(timezone.utc)
            
            # Fetch the document to get the topic and industry
            from app.models.document import Document
            doc = await session.get(Document, job.document_id)
            if doc:
                # Bridge to MOCK_REPORTS so it shows up in the frontend Dashboard / Reports list
                from app.api.v1.endpoints.reports import MOCK_REPORTS
                
                # Check if it was triggered for a mock report (stored in job.workflow)
                doc_str_id = job.workflow if job.workflow else str(doc.id)
                
                # Add/Update the mock report
                MOCK_REPORTS[doc_str_id] = {
                    "id": doc_str_id, 
                    "title": doc.title or job.topic, 
                    "version": "2.0" if job.workflow else "1.0",
                    "status": "AI Reviewed", 
                    "humanStatus": "AI Reviewed Complete", 
                    "aiScore": 88, 
                    "aiGrade": "Silver",
                    "commentCount": 0, 
                    "lastUpdated": datetime.now(timezone.utc).isoformat() + "Z", 
                    "publishReady": False, 
                    "aiReview": None,
                    "reportContent": {
                        "brand": "GateX", 
                        "label": "AI Reviewed", 
                        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), 
                        "sections": [
                            {"heading": "Executive Summary", "body": f"This is an AI generated report about '{doc.title or job.topic}' in the {doc.industry or 'General'} industry."}
                        ]
                    },
                    "comments": []
                }
            await session.commit()

class GenerationService:
    def __init__(self):
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
        
        # Dispatch to execution backend (simulated asynchronously)
        success = await self.worker.dispatch(job)
        if not success:
            job.status = JobStatusType.failed
            job.errors = "Failed to dispatch to worker"
            await db.commit()
        else:
            # Trigger the async simulation
            asyncio.create_task(simulate_job_execution(job.id))
            
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
        asyncio.create_task(simulate_job_execution(job.id))
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
