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
    # Return mock data matching the frontend's Report interface to allow UI testing of tags
    mock_reports = [
        {
            "id": "doc-1111-approved",
            "title": "Nuclear Fusion Commercialization",
            "version": "1.2",
            "status": "Approved",
            "humanStatus": "Final Review Complete",
            "aiScore": 92,
            "aiGrade": "Gold",
            "commentCount": 0,
            "lastUpdated": "2026-06-30T10:00:00Z",
            "publishReady": True,
            "aiReview": None,
            "reportContent": {"brand": "GateX", "label": "Approved", "date": "2026-06-30", "sections": []},
            "comments": []
        },
        {
            "id": "doc-2222-rejected",
            "title": "Quantum Computing Market Outlook",
            "version": "1.0",
            "status": "Rejected",
            "humanStatus": "Needs rewrite",
            "aiScore": 45,
            "aiGrade": "Bronze",
            "commentCount": 5,
            "lastUpdated": "2026-06-29T14:30:00Z",
            "publishReady": False,
            "aiReview": None,
            "reportContent": {"brand": "GateX", "label": "Rejected", "date": "2026-06-29", "sections": []},
            "comments": []
        },
        {
            "id": "doc-3333-review",
            "title": "Middle East AI Strategies",
            "version": "2.1",
            "status": "Needs Human Review",
            "humanStatus": "Pending Editorial Approval",
            "aiScore": 85,
            "aiGrade": "Silver",
            "commentCount": 2,
            "lastUpdated": "2026-06-30T19:00:00Z",
            "publishReady": False,
            "aiReview": None,
            "reportContent": {"brand": "GateX", "label": "Review", "date": "2026-06-30", "sections": []},
            "comments": []
        }
    ]
    
    return success_response(
        data=mock_reports,
        message="Fetched mock reports successfully",
        metadata={"total": 3, "offset": page.offset, "limit": page.limit, "has_more": False}
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
    # Return mock data matching the frontend's Report interface
    mock_reports = {
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
    
    report = mock_reports.get(document_id, mock_reports["doc-3333-review"])
    return success_response(data=report, message="Fetched report details")

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
