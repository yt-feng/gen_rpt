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

import httpx
from app.core.config import settings

class GitHubActionsWorker(WorkerInterface):
    async def dispatch(self, job: GenerationJob) -> bool:
        if not settings.GITHUB_TOKEN:
            print("GITHUB_TOKEN not set. Cannot dispatch to GitHub Actions.")
            return False
            
        # Get the slug from the document
        from app.database.session import async_session_maker
        from app.models.document import Document
        
        slug = ""
        async with async_session_maker() as session:
            doc = await session.get(Document, job.document_id)
            if doc and doc.slug:
                slug = doc.slug
            else:
                slug = f"doc-{str(job.id)[:8]}"

        url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/actions/workflows/generate_deep_research_v2.yml/dispatches"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        payload = {
            "ref": "main",
            "inputs": {
                "topic": job.topic,
                "slug": slug
            }
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 204:
                    print(f"Successfully dispatched GitHub Action for Job {job.id}")
                    return True
                else:
                    print(f"Failed to dispatch GitHub Action: {resp.status_code} - {resp.text}")
                    return False
        except Exception as e:
            print(f"Exception dispatching GitHub Action: {e}")
            return False

    async def cancel(self, job: GenerationJob) -> bool:
        print(f"Cancelling GitHub Action for Job {job.id} (not implemented)")
        return True

async def poll_r2_for_completion(job_id: uuid.UUID):
    """
    Polls Cloudflare R2 every 30 seconds for the report.md file.
    If found, marks the job as completed and injects it into MOCK_REPORTS for the UI.
    Times out after 45 minutes.
    """
    from app.database.session import async_session_maker
    from app.storage.provider import storage_provider
    from app.models.document import Document
    
    # Wait initially to allow the workflow to start
    await asyncio.sleep(10)
    
    # Mark as running
    async with async_session_maker() as session:
        job = await session.get(GenerationJob, job_id)
        if job and job.status == JobStatusType.pending:
            job.status = JobStatusType.running
            await session.commit()
            
    # Get slug
    slug = ""
    async with async_session_maker() as session:
        doc = await session.get(Document, job.document_id)
        slug = doc.slug if (doc and doc.slug) else f"doc-{str(job.id)[:8]}"

    # Poll R2
    max_attempts = 90  # 90 * 30s = 45 minutes
    r2_path = f"reports_web/{slug}/report.md"
    
    for attempt in range(max_attempts):
        exists = await storage_provider.exists(r2_path)
        if exists:
            print(f"Report found in R2 for Job {job_id} at {r2_path}!")
            break
        await asyncio.sleep(30)
    else:
        print(f"Timed out waiting for report in R2 for Job {job_id}")
        async with async_session_maker() as session:
            job = await session.get(GenerationJob, job_id)
            if job:
                job.status = JobStatusType.failed
                job.errors = "Timed out waiting for GH Action to produce report.md in R2."
                await session.commit()
        return

    # Job is completed!
    async with async_session_maker() as session:
        job = await session.get(GenerationJob, job_id)
        if job and job.status == JobStatusType.running:
            job.status = JobStatusType.completed
            job.completed = datetime.now(timezone.utc)
            
            doc = await session.get(Document, job.document_id)
            if doc:
                from app.api.v1.endpoints.reports import MOCK_REPORTS
                doc_str_id = job.workflow if job.workflow else str(doc.id)
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
                            {"heading": "Executive Summary", "body": f"This is an AI generated report about '{doc.title or job.topic}'. The full content is in R2 at {r2_path}."}
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
            # Trigger the async background poller
            asyncio.create_task(poll_r2_for_completion(job.id))
            
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
        asyncio.create_task(poll_r2_for_completion(job.id))
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
