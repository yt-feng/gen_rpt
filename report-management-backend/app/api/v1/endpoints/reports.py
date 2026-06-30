from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import List

from app.api.deps import get_db, PageParams, FilterParams, get_current_user_placeholder
from app.core.responses import APIResponse, success_response, error_response

router = APIRouter()

@router.get("/", response_model=APIResponse[list])
async def list_reports(
    page: PageParams = Depends(),
    filters: FilterParams = Depends(),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    List all reports based on filters and pagination.
    """
# Global mock state to allow frontend updates to persist across API calls
MOCK_REPORTS = {
    "doc-1111-approved": {
        "id": "doc-1111-approved", "title": "Nuclear Fusion Commercialization", "version": "1.2",
        "status": "Approved", "humanStatus": "Final Review Complete", "aiScore": 92, "aiGrade": "Gold",
        "commentCount": 0, "lastUpdated": "2026-06-30T10:00:00Z", "publishReady": True, "aiReview": None,
        "reportContent": {"brand": "GateX", "label": "Approved", "date": "2026-06-30", "sections": [{"heading": "Executive Summary", "body": "Mock content for approved report."}]},
        "comments": []
    },
    "doc-2222-rejected": {
        "id": "doc-2222-rejected", "title": "Quantum Computing Market Outlook", "version": "1.0",
        "status": "Rejected", "humanStatus": "Needs rewrite", "aiScore": 45, "aiGrade": "Bronze",
        "commentCount": 5, "lastUpdated": "2026-06-29T14:30:00Z", "publishReady": False, "aiReview": None,
        "reportContent": {"brand": "GateX", "label": "Rejected", "date": "2026-06-29", "sections": [{"heading": "Executive Summary", "body": "Mock content for rejected report."}]},
        "comments": []
    },
    "doc-3333-review": {
        "id": "doc-3333-review", "title": "Middle East AI Strategies", "version": "2.1",
        "status": "Needs Human Review", "humanStatus": "Pending Editorial Approval", "aiScore": 85, "aiGrade": "Silver",
        "commentCount": 2, "lastUpdated": "2026-06-30T19:00:00Z", "publishReady": False, "aiReview": None,
        "reportContent": {"brand": "GateX", "label": "Review", "date": "2026-06-30", "sections": [{"heading": "Executive Summary", "body": "Mock content for review report."}]},
        "comments": []
    }
}

@router.get("/", response_model=APIResponse[list])
async def list_reports(
    page: PageParams = Depends(),
    filters: FilterParams = Depends(),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    List all reports based on filters and pagination.
    """
    reports_list = list(MOCK_REPORTS.values())
    
    # Simple mock filtering to support frontend tabs
    if filters.status:
        reports_list = [r for r in reports_list if r["status"].lower() == filters.status.lower()]
        
    return success_response(
        data=reports_list,
        message="Fetched mock reports successfully",
        metadata={"total": len(reports_list), "offset": page.offset, "limit": page.limit, "has_more": False}
    )

@router.get("/{document_id}", response_model=APIResponse[dict])
async def get_report_details(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Get detailed metadata for a specific report document.
    """
    report = MOCK_REPORTS.get(document_id, MOCK_REPORTS["doc-3333-review"])
    return success_response(data=report, message="Fetched report details")

from pydantic import BaseModel
from typing import Optional

class StatusUpdatePayload(BaseModel):
    status: Optional[str] = None
    humanStatus: Optional[str] = None
    publishReady: Optional[bool] = None

@router.post("/{document_id}/status", response_model=APIResponse[dict])
async def update_report_status(
    document_id: str,
    payload: StatusUpdatePayload,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Update the status of a specific report.
    """
    report = MOCK_REPORTS.get(document_id)
    if not report:
        # fallback for new generated jobs
        report = MOCK_REPORTS["doc-3333-review"].copy()
        report["id"] = document_id
        MOCK_REPORTS[document_id] = report
        
    if payload.status is not None:
        report["status"] = payload.status
    if payload.humanStatus is not None:
        report["humanStatus"] = payload.humanStatus
    if payload.publishReady is not None:
        report["publishReady"] = payload.publishReady
        
    return success_response(data=report, message="Report status updated")

@router.get("/{document_id}/download-url", response_model=APIResponse[dict])
async def get_report_download_url(
    document_id: UUID,
    file_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Generate a signed URL for secure downloading of report artifacts.
    """
    from app.services.storage import storage_service
    url = await storage_service.get_signed_url(db, file_id)
    if not url:
        return error_response(message="File not found or unauthorized")
        
    return success_response(data={"url": url}, message="Generated signed URL successfully")
