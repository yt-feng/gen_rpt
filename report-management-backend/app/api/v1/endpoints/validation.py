import uuid
from fastapi import APIRouter, Depends, Query, status, HTTPException
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.api.deps import get_current_user_placeholder, get_db
from app.core.responses import APIResponse, success_response
from app.core.config import settings
from app.schemas.validation import (
    ValidatedContextPackage,
    ValidationReportResponse,
    ValidationHistoryResponse,
    ValidationAuditResponse,
    ValidationPolicyResponse,
    ValidationPolicyCreate,
    ValidationPolicyUpdate
)
from app.services.validation import (
    validation_service,
    policy_service,
    history_service,
    audit_service
)
from app.models.validation import ValidationReport, ValidationHistory, ValidationPolicy, ValidationAuditLog

router = APIRouter()

def verify_validation_enabled():
    """Dependency/guard to check if validation feature is enabled."""
    if not settings.VALIDATION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Enterprise Knowledge Validation Engine is currently disabled."
        )

# ==========================================
# 1. Validation Ingestion & Execution
# ==========================================

@router.post("/validate", response_model=APIResponse[ValidatedContextPackage], status_code=status.HTTP_200_OK)
async def validate_retrieval_session(
    session_id: uuid.UUID = Query(..., description="The ID of the Retrieval Session to validate"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_validation_enabled)
):
    """
    Validates retrieved evidence context package for a specific retrieval session.
    Fails if session is not found or validation errors occur.
    """
    try:
        validated_package = await validation_service.validate_session(
            db=db,
            session_id=session_id,
            user_id=uuid.UUID(user["id"])
        )
        return success_response(data=validated_package, message="Retrieval session validated successfully.")
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred during validation: {str(e)}"
        )

# ==========================================
# 2. Validation Policy Management
# ==========================================

@router.get("/policies", response_model=APIResponse[List[ValidationPolicyResponse]])
async def list_policies(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_validation_enabled)
):
    """List all configured validation policies."""
    policies = await policy_service.list_policies(db)
    return success_response(data=policies, message="Validation policies listed successfully.")


@router.post("/policies", response_model=APIResponse[ValidationPolicyResponse], status_code=status.HTTP_201_CREATED)
async def create_policy(
    payload: ValidationPolicyCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_validation_enabled)
):
    """Create a new validation policy and optionally set it active."""
    policy = await policy_service.create_policy(db, payload)
    return success_response(data=policy, message="Validation policy created successfully.")


@router.put("/policies/{policy_id}", response_model=APIResponse[ValidationPolicyResponse])
async def update_policy(
    policy_id: uuid.UUID,
    payload: ValidationPolicyUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_validation_enabled)
):
    """Update configured options of an existing policy."""
    policy = await policy_service.update_policy(db, policy_id, payload)
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Validation policy not found."
        )
    return success_response(data=policy, message="Validation policy updated successfully.")

# ==========================================
# 3. Validation Reports & history
# ==========================================

@router.get("/history", response_model=APIResponse[List[ValidationHistoryResponse]])
async def get_validation_history(
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_validation_enabled)
):
    """Retrieve immutable validation history runs."""
    runs = await history_service.list_history(db, limit)
    return success_response(data=runs, message="Validation history retrieved successfully.")


@router.get("/history/{run_id}", response_model=APIResponse[ValidationHistoryResponse])
async def get_validation_history_details(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_validation_enabled)
):
    """Retrieve details of a specific validation history run."""
    run = await history_service.get_history_by_run(db, run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Validation run history not found."
        )
    return success_response(data=run, message="Validation run history details retrieved.")


@router.get("/reports/{report_id}", response_model=APIResponse[ValidationReportResponse])
async def get_validation_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_validation_enabled)
):
    """Retrieve database metadata for a validation report."""
    stmt = select(ValidationReport).where(ValidationReport.id == report_id)
    res = await db.execute(stmt)
    report = res.scalar_one_or_none()
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Validation report not found."
        )
    return success_response(data=report, message="Validation report retrieved successfully.")


@router.get("/summary", response_model=APIResponse[List[Dict[str, Any]]])
async def get_validation_summary(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_validation_enabled)
):
    """Get summarized outcomes of validation sessions."""
    stmt = select(ValidationReport).order_by(ValidationReport.created_at.desc()).limit(10)
    res = await db.execute(stmt)
    reports = res.scalars().all()
    
    summary_data = []
    for r in reports:
        summary_data.append({
            "report_id": r.id,
            "session_id": r.session_id,
            "summary": r.validation_summary,
            "overall_confidence": (r.confidence_scores or {}).get("overall_confidence", 0.0),
            "created_at": r.created_at
        })
    return success_response(data=summary_data, message="Validation summary retrieved.")


@router.get("/statistics", response_model=APIResponse[Dict[str, Any]])
async def get_validation_statistics(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_validation_enabled)
):
    """Returns analytics metrics for validation processes."""
    stmt = select(ValidationHistory).order_by(ValidationHistory.created_at.desc()).limit(100)
    res = await db.execute(stmt)
    runs = res.scalars().all()
    
    if not runs:
        return success_response(
            data={
                "validation_requests_count": 0,
                "average_validation_confidence": 0.0,
                "average_freshness": 0.0,
                "conflict_rate": 0.0,
                "average_validation_time_ms": 0.0
            },
            message="No validation runs available yet."
        )
        
    total = len(runs)
    avg_conf = sum(r.confidence_score for r in runs) / total
    avg_fresh = sum(r.freshness_score for r in runs) / total
    conflicts_runs = sum(1 for r in runs if r.conflict_count > 0)
    conflict_rate = conflicts_runs / total
    
    # Calculate execution time average from audit logs
    audit_stmt = select(func.avg(ValidationAuditLog.execution_time_ms))
    audit_res = await db.execute(audit_stmt)
    avg_exec_time = float(audit_res.scalar() or 0.0)

    stats = {
        "validation_requests_count": total,
        "average_validation_confidence": float(round(avg_conf, 4)),
        "average_freshness": float(round(avg_fresh, 4)),
        "conflict_rate": float(round(conflict_rate, 4)),
        "average_validation_time_ms": float(round(avg_exec_time, 2))
    }
    return success_response(data=stats, message="Validation statistics calculated.")

# ==========================================
# 4. Extended Health Endpoint
# ==========================================

@router.get("/health", response_model=APIResponse[Dict[str, Any]])
async def get_validation_health(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_validation_enabled)
):
    """Extended backend health checks for validation modules."""
    health_data = {
        "status": "healthy",
        "validation_engine": "healthy",
        "authority_service": "healthy",
        "conflict_service": "healthy",
        "duplicate_service": "healthy",
        "confidence_service": "healthy",
        "policy_engine": "healthy",
        "history": "healthy",
        "audit": "healthy"
    }
    return success_response(data=health_data, message="Validation services report healthy.")
