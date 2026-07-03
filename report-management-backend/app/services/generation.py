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

async def _load_report_payload_from_r2(slug: str, topic: str) -> dict:
    """
    Tries to load the web_report_payload.json from R2 to get real report content.
    The GitHub Actions workflow often prepends a date (YYYY-MM-DD-) to the slug in R2.
    """
    from app.storage.provider import storage_provider
    import json
    from anyio import to_thread
    
    if not storage_provider.is_configured:
        return {}

    def _find_and_download():
        try:
            # Check reports/
            res = storage_provider.s3_client.list_objects_v2(
                Bucket=storage_provider.bucket, Prefix='reports/', Delimiter='/'
            )
            for prefix_obj in res.get('CommonPrefixes', []):
                folder = prefix_obj['Prefix']
                if slug in folder:
                    path = f"{folder}metadata/web_report_payload.json"
                    response = storage_provider.s3_client.get_object(Bucket=storage_provider.bucket, Key=path)
                    return response['Body'].read()
                    
            # Check reports_web/
            res2 = storage_provider.s3_client.list_objects_v2(
                Bucket=storage_provider.bucket, Prefix='reports_web/', Delimiter='/'
            )
            for prefix_obj in res2.get('CommonPrefixes', []):
                folder = prefix_obj['Prefix']
                if slug in folder:
                    path = f"{folder}web_report_payload.json"
                    response = storage_provider.s3_client.get_object(Bucket=storage_provider.bucket, Key=path)
                    return response['Body'].read()
        except Exception as e:
            print(f"[poll_r2] Error finding payload for {slug}: {e}")
        return None

    data = await to_thread.run_sync(_find_and_download)
    if data:
        try:
            return json.loads(data.decode("utf-8"))
        except Exception as e:
            print(f"[poll_r2] Failed to parse payload for {slug}: {e}")

    return {}


def _build_mock_report_entry(
    doc_str_id: str,
    title: str,
    slug: str,
    payload: dict,
) -> dict:
    """
    Builds a MOCK_REPORTS entry from the report payload or a minimal stub.
    Pulls sections from the payload if present so the frontend shows real content.
    """
    now = datetime.now(timezone.utc)

    # Try to extract sections from the web report payload
    sections = []
    raw_sections = (
        payload.get("sections")
        or payload.get("report", {}).get("sections")
        or []
    )
    for s in raw_sections:
        heading = s.get("heading") or s.get("title") or s.get("id", "Section")
        body = s.get("body") or s.get("content") or s.get("html") or s.get("markdown") or ""
        # Strip any HTML tags for MOCK_REPORTS text preview
        import re
        body_text = re.sub(r"<[^>]+>", "", str(body))[:2000]
        sections.append({"heading": heading, "body": body_text})

    if not sections:
        sections = [{
            "heading": "Executive Summary",
            "body": f"AI-generated report on '{title}'. View the full HTML report via the report viewer."
        }]

    # Determine AI review info from payload if available
    review_info = payload.get("review") or {}
    ai_score = review_info.get("overall_score") or 85
    ai_grade = review_info.get("grade") or "Silver"

    return {
        "id": doc_str_id,
        "title": title,
        "version": "1.0",
        "status": "Generated",
        "humanStatus": "Pending Review",
        "aiScore": ai_score,
        "aiGrade": ai_grade,
        "commentCount": 0,
        "lastUpdated": now.isoformat() + "Z",
        "publishReady": False,
        "aiReview": None,
        "slug": slug,
        "reportContent": {
            "brand": payload.get("brand") or "GateX Intelligence",
            "label": payload.get("label") or "Deep Research",
            "date": now.strftime("%B %d, %Y"),
            "sections": sections,
        },
        "comments": [],
    }


async def poll_r2_for_completion(job_id: uuid.UUID):
    """
    Polls Cloudflare R2 every 30 seconds for the uploaded report.
    Checks BOTH possible paths:
      - reports/{slug}/current/report.md  (upload_report.py destination)
      - reports_web/{slug}/report.md      (legacy / direct path)
    If found, marks the job as completed and injects it into MOCK_REPORTS.
    Times out after 45 minutes.
    """
    from app.database.session import async_session_maker
    from app.storage.provider import storage_provider
    from app.models.document import Document

    # Wait briefly to allow the workflow to start
    await asyncio.sleep(15)

    # Mark as running
    async with async_session_maker() as session:
        job = await session.get(GenerationJob, job_id)
        if not job:
            print(f"[poll_r2] Job {job_id} not found.")
            return
        if job.status == JobStatusType.pending:
            job.status = JobStatusType.running
            await session.commit()

    # Get slug from the document
    slug = ""
    async with async_session_maker() as session:
        job = await session.get(GenerationJob, job_id)
        if not job:
            return
        doc = await session.get(Document, job.document_id)
        slug = doc.slug if (doc and doc.slug) else f"doc-{str(job.id)[:8]}"
        topic = job.topic or "Unknown Topic"
        doc_id = str(doc.id) if doc else str(job.document_id)

    # Two candidate R2 paths to check:
    # 1. The path used by upload_report.py  (reports/{slug}/current/report.md)
    # 2. The legacy reports_web path         (reports_web/{slug}/report.md)
    r2_paths = [
        f"reports/{slug}/current/report.md",
        f"reports_web/{slug}/report.md",
    ]

    max_attempts = 90  # 90 × 30 s = 45 min
    found_path = None

    for attempt in range(max_attempts):
        for candidate in r2_paths:
            try:
                exists = await storage_provider.exists(candidate)
                if exists:
                    found_path = candidate
                    break
            except Exception as e:
                print(f"[poll_r2] Error checking {candidate}: {e}")
        if found_path:
            print(f"[poll_r2] Report found at {found_path} for job {job_id}")
            break
        print(f"[poll_r2] Attempt {attempt+1}/{max_attempts}: report not yet in R2 for job {job_id}")
        await asyncio.sleep(30)
    else:
        print(f"[poll_r2] Timed out waiting for report in R2 for job {job_id}")
        async with async_session_maker() as session:
            job = await session.get(GenerationJob, job_id)
            if job:
                job.status = JobStatusType.failed
                job.errors = "Timed out: report.md not found in R2 after 45 min."
                await session.commit()
        return

    # Load the real payload from R2 (for richer MOCK_REPORTS entry)
    payload = await _load_report_payload_from_r2(slug, topic)

    # Mark job completed and inject into MOCK_REPORTS
    async with async_session_maker() as session:
        job = await session.get(GenerationJob, job_id)
        if job and job.status == JobStatusType.running:
            job.status = JobStatusType.completed
            job.completed = datetime.now(timezone.utc)

            doc = await session.get(Document, job.document_id)
            title = (doc.title if doc else None) or topic
            doc_str_id = slug  # Use slug as the MOCK_REPORTS key so frontend can look it up

            from app.api.v1.endpoints.reports import MOCK_REPORTS
            entry = _build_mock_report_entry(doc_str_id, title, slug, payload)
            MOCK_REPORTS[doc_str_id] = entry
            # Also store under the UUID so both IDs resolve
            MOCK_REPORTS[doc_id] = entry

            print(f"[poll_r2] MOCK_REPORTS updated: {doc_str_id}")
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
