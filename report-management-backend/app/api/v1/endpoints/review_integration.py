import uuid
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user_placeholder
from app.core.responses import APIResponse, success_response
from app.schemas.review_integration import (
    ReviewSnapshotResponse,
    ReviewAnalyticsResponse,
    EvidenceViewerData,
    CitationVerificationSummary,
    ValidationDashboardData
)
from app.services.review_integration import (
    evidence_verification_service,
    citation_verification_service,
    traceability_service,
    review_snapshot_service,
    evidence_viewer_service,
    knowledge_browser_service,
    validation_dashboard_service,
    review_analytics_service
)

router = APIRouter()

@router.get("/viewer/{version_id}", response_model=APIResponse[EvidenceViewerData])
async def get_evidence_viewer_data(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """Retrieve verified evidence, chunks, and reports for a document version."""
    try:
        data = await evidence_viewer_service.get_viewer_data(db, version_id)
        return success_response(data=data, message="Fetched evidence viewer data")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/browser", response_model=APIResponse[List[dict]])
async def browse_knowledge(
    query: Optional[str] = None,
    collection_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """Browse documents and collections metadata (read-only)."""
    data = await knowledge_browser_service.browse_knowledge(db, query, collection_id)
    return success_response(data=data, message="Fetched browse results")

@router.get("/traceability/{version_id}", response_model=APIResponse[dict])
async def get_source_traceability(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """Provides section-to-source traceability matrix mapping."""
    try:
        data = await traceability_service.get_traceability(db, version_id)
        return success_response(data=data, message="Fetched traceability matrix")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/verification/{version_id}", response_model=APIResponse[dict])
async def get_evidence_verification(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """Detect unsupported claims and verify overall evidence support."""
    try:
        data = await evidence_verification_service.verify_evidence(db, version_id)
        return success_response(data=data, message="Executed evidence verification")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/citations/{version_id}", response_model=APIResponse[CitationVerificationSummary])
async def verify_citations(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """Scan and verify cited documents, freshness, and authority metrics."""
    data = await citation_verification_service.verify_citations(db, version_id)
    return success_response(data=data, message="Executed citation verification")

@router.get("/comparison/{version_id}", response_model=APIResponse[dict])
async def get_evidence_comparison(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """Compare generated text vs original sources (read-only comparison visualization)."""
    # Simple simulated read-only comparison returning node changes
    data = {
        "version_id": str(version_id),
        "supported_blocks": [],
        "low_confidence_blocks": []
    }
    return success_response(data=data, message="Fetched comparison payload")

@router.get("/dashboard/{version_id}", response_model=APIResponse[ValidationDashboardData])
async def get_validation_dashboard(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """Retrieve metrics for the review validation dashboard (coverage, freshness, duplicate ratio)."""
    data = await validation_dashboard_service.get_dashboard_data(db, version_id)
    return success_response(data=data, message="Fetched validation dashboard metrics")

@router.get("/analytics", response_model=APIResponse[dict])
async def get_review_analytics(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """Collect global review metrics (total reviewed, avg confidence)."""
    data = await review_analytics_service.get_analytics(db)
    return success_response(data=data, message="Fetched review analytics")

@router.post("/snapshots/{version_id}", response_model=APIResponse[ReviewSnapshotResponse])
async def create_review_snapshot(
    version_id: uuid.UUID,
    ai_review_id: Optional[uuid.UUID] = None,
    human_review_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """Generate an immutable snapshot of review results and evidence state."""
    try:
        reviewer_id = uuid.UUID(user["id"])
        snapshot = await review_snapshot_service.create_review_snapshot(
            db=db,
            version_id=version_id,
            reviewer_id=reviewer_id,
            ai_review_id=ai_review_id,
            human_review_id=human_review_id
        )
        return success_response(data=snapshot, message="Review snapshot created")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/snapshots/{snapshot_id}", response_model=APIResponse[dict])
async def get_review_snapshot(
    snapshot_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """Retrieve an immutable review snapshot details."""
    try:
        data = await review_snapshot_service.get_review_snapshot(db, snapshot_id)
        return success_response(data=data, message="Fetched review snapshot details")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
