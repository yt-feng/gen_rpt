"""
GateX Publishing API Endpoints
================================
Provides the external publishing interface for the GateX (MENA Compass) integration.

Endpoints:
  POST   /api/v1/publish/{report_id}           — Trigger GateX publish flow
  POST   /api/v1/unpublish/{report_id}         — Trigger unpublish (abstraction layer)
  GET    /api/v1/publish/{report_id}/status    — Get current publish status + history
  GET    /api/v1/publish/history               — List all publication records
  GET    /api/v1/publish/logs/{report_id}      — Get audit logs for a report

These endpoints drive the existing frontend Publish/Unpublish buttons.
No UI changes are required.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from uuid import UUID
import uuid
import hashlib

from app.api.deps import get_db, get_current_user_placeholder
from app.core.responses import APIResponse, success_response, error_response
from app.logging.logger import logger

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _coerce_uuid(raw_id: str) -> uuid.UUID:
    """Converts a string (UUID or mock slug) to a UUID, deterministically."""
    try:
        return uuid.UUID(raw_id)
    except ValueError:
        m = hashlib.md5()
        m.update(raw_id.encode("utf-8"))
        return uuid.UUID(m.hexdigest())


# ---------------------------------------------------------------------------
# POST /publish/{report_id}
# ---------------------------------------------------------------------------
@router.post("/publish/{report_id}", response_model=APIResponse[dict])
async def publish_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
):
    """
    Trigger the GateX publishing pipeline for a specific report.

    Executes the full 15-step flow:
      1. Validate eligibility (Approved status, PDF, cover image present)
      2. Check for duplicate publication
      3. Upload PDF to GateX storage via presigned URL
      4. Upload cover image to GateX storage via presigned URL
      5. Submit report metadata to POST /api/reports/bulk
      6. Store external identifiers in Supabase
      7. Mark report as Published
      8. Record audit logs

    When GATEX_ENABLE_PUBLISHING=false (default), runs eligibility checks only
    and returns a dry-run result without making any external calls.
    """
    from app.api.v1.endpoints.reports import MOCK_REPORTS
    from app.services.publish_orchestrator import publish_orchestrator

    report = MOCK_REPORTS.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found.")

    logger.info(f"Publish requested: report_id={report_id} by user={user.get('id')}")

    result = await publish_orchestrator.publish(
        db=db,
        report=report,
        report_id=report_id,
        actor_id=user.get("id", "00000000-0000-0000-0000-000000000000"),
    )

    if result.success:
        return success_response(
            data={
                "report_id": result.report_id,
                "external_report_id": result.external_report_id,
                "external_status": result.external_status,
                "processing_status": result.processing_status,
                "publish_status": result.publish_status,
                "duration_ms": result.duration_ms,
                "audit_trail": result.audit_trail,
                "message": f"Report successfully published to GateX (MENA Compass). External ID: {result.external_report_id}. Status: {result.external_status}.",
            },
            message=f"✅ Report published to GateX. External ID: {result.external_report_id}.",
        )
    else:
        raise HTTPException(
            status_code=422,
            detail={
                "error": result.error or "Publish failed.",
                "report_id": result.report_id,
                "publish_status": result.publish_status,
                "audit_trail": result.audit_trail,
            }
        )


# ---------------------------------------------------------------------------
# POST /unpublish/{report_id}
# ---------------------------------------------------------------------------
@router.post("/unpublish/{report_id}", response_model=APIResponse[dict])
async def unpublish_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
):
    """
    Trigger the unpublish workflow for a report.

    NOTE: GateX does not currently provide an official unpublish/delete API endpoint.
    This endpoint:
      - Updates the internal publication record to 'unpublished'
      - Updates the report status to 'Rejected/Unpublished' in the internal state
      - Records an audit log
      - Returns a clear message requesting manual removal from the MENA Compass admin panel

    This abstraction layer is ready to be wired to a real GateX delete endpoint
    when/if the MENA Compass team provides one.
    """
    from app.api.v1.endpoints.reports import MOCK_REPORTS
    from app.services.publish_orchestrator import publish_orchestrator

    report = MOCK_REPORTS.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found.")

    logger.info(f"Unpublish requested: report_id={report_id} by user={user.get('id')}")

    result = await publish_orchestrator.unpublish(
        db=db,
        report=report,
        report_id=report_id,
        actor_id=user.get("id", "00000000-0000-0000-0000-000000000000"),
    )

    return success_response(
        data=result,
        message="Unpublish processed. See 'action_required' for any manual steps needed.",
    )


# ---------------------------------------------------------------------------
# GET /publish/{report_id}/status
# ---------------------------------------------------------------------------
@router.get("/publish/{report_id}/status", response_model=APIResponse[dict])
async def get_publish_status(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
):
    """
    Returns the current GateX publication status for a report,
    including the most recent external identifiers and any errors.
    """
    from app.models.workflow import GateXPublication
    from app.api.v1.endpoints.reports import MOCK_REPORTS

    doc_uuid = _coerce_uuid(report_id)

    stmt = (
        select(GateXPublication)
        .where(GateXPublication.document_id == doc_uuid)
        .order_by(desc(GateXPublication.created_at))
    )
    result = await db.execute(stmt)
    records = result.scalars().all()

    report = MOCK_REPORTS.get(report_id, {})

    if not records:
        return success_response(
            data={
                "report_id": report_id,
                "publish_status": report.get("publishStatus", "not_published"),
                "internal_status": report.get("status", "Unknown"),
                "external_report_id": None,
                "history": [],
            },
            message="No publication records found for this report.",
        )

    latest = records[0]
    return success_response(
        data={
            "report_id": report_id,
            "publish_status": latest.publish_status,
            "internal_status": report.get("status", "Unknown"),
            "external_report_id": latest.external_report_id,
            "original_object_key": latest.original_object_key,
            "cover_image_key": latest.cover_image_key,
            "published_at": latest.published_at.isoformat() if latest.published_at else None,
            "duration_ms": latest.publish_duration_ms,
            "retry_count": latest.retry_count,
            "errors": latest.errors,
            "last_synced_at": latest.last_synced_at.isoformat() if latest.last_synced_at else None,
            "history": [
                {
                    "id": str(r.id),
                    "publish_status": r.publish_status,
                    "external_report_id": r.external_report_id,
                    "published_at": r.published_at.isoformat() if r.published_at else None,
                    "errors": r.errors,
                }
                for r in records
            ],
        },
        message="Fetched publish status.",
    )


# ---------------------------------------------------------------------------
# GET /publish/history
# ---------------------------------------------------------------------------
@router.get("/publish/history", response_model=APIResponse[list])
async def get_publish_history(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
):
    """
    Returns a list of all GateX publication records across all reports,
    ordered by most recent first. Useful for admin monitoring.
    """
    from app.models.workflow import GateXPublication

    stmt = select(GateXPublication).order_by(desc(GateXPublication.created_at)).limit(100)
    result = await db.execute(stmt)
    records = result.scalars().all()

    history = [
        {
            "id": str(r.id),
            "document_id": str(r.document_id),
            "external_report_id": r.external_report_id,
            "publish_status": r.publish_status,
            "original_object_key": r.original_object_key,
            "cover_image_key": r.cover_image_key,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "duration_ms": r.publish_duration_ms,
            "retry_count": r.retry_count,
            "errors": r.errors,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]

    return success_response(
        data=history,
        message=f"Fetched {len(history)} publication records.",
        metadata={"total": len(history)},
    )


# ---------------------------------------------------------------------------
# GET /publish/logs/{report_id}
# ---------------------------------------------------------------------------
@router.get("/publish/logs/{report_id}", response_model=APIResponse[list])
async def get_publish_logs(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
):
    """
    Returns audit log entries associated with GateX publishing for a specific report.
    Logs are sourced from the system audit_logs table, filtered to gatex_publications events.
    """
    from app.models.system import AuditLog

    # Retrieve all GateXPublication IDs for this report first
    from app.models.workflow import GateXPublication

    doc_uuid = _coerce_uuid(report_id)
    pub_stmt = select(GateXPublication.id).where(GateXPublication.document_id == doc_uuid)
    pub_result = await db.execute(pub_stmt)
    pub_ids = [row[0] for row in pub_result.all()]

    if not pub_ids:
        return success_response(data=[], message="No publish audit logs found for this report.")

    log_stmt = (
        select(AuditLog)
        .where(AuditLog.table_name == "gatex_publications")
        .where(AuditLog.record_id.in_(pub_ids))
        .order_by(desc(AuditLog.timestamp))
    )
    log_result = await db.execute(log_stmt)
    logs = log_result.scalars().all()

    return success_response(
        data=[
            {
                "id": str(log.id),
                "action": log.action,
                "old_data": log.old_data,
                "new_data": log.new_data,
                "changed_by": str(log.changed_by) if log.changed_by else None,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            }
            for log in logs
        ],
        message=f"Fetched {len(logs)} audit log entries.",
    )


# ---------------------------------------------------------------------------
# GET /publish/taxonomy/status   (admin/debug)
# ---------------------------------------------------------------------------
@router.get("/publish/taxonomy/status", response_model=APIResponse[dict])
async def get_taxonomy_cache_status(
    user: dict = Depends(get_current_user_placeholder),
):
    """
    Returns the current state of the GateX taxonomy cache.
    Useful for diagnosing category/tag resolution issues.
    """
    from app.services.gatex_taxonomy import get_cache_status
    return success_response(
        data=get_cache_status(),
        message="GateX taxonomy cache status.",
    )


# ---------------------------------------------------------------------------
# POST /publish/taxonomy/refresh  (admin)
# ---------------------------------------------------------------------------
@router.post("/publish/taxonomy/refresh", response_model=APIResponse[dict])
async def refresh_taxonomy_cache(
    user: dict = Depends(get_current_user_placeholder),
):
    """
    Forces a refresh of the GateX taxonomy cache (categories, tags, regions, industries).
    """
    from app.services import gatex_taxonomy
    await gatex_taxonomy.get_categories(force_refresh=True)
    await gatex_taxonomy.get_tags(force_refresh=True)
    await gatex_taxonomy.get_regions(force_refresh=True)
    await gatex_taxonomy.get_industries(force_refresh=True)
    return success_response(
        data=gatex_taxonomy.get_cache_status(),
        message="GateX taxonomy cache refreshed successfully.",
    )
