from fastapi import APIRouter, Depends, Query, status, HTTPException
from typing import List, Optional, Dict, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_placeholder, get_db
from app.core.responses import APIResponse, success_response
from app.services.knowledge import verify_knowledge_enabled
from app.schemas.knowledge_intelligence import (
    IntelligenceAnalyticsResponse,
    IntelligenceRecommendationResponse,
    KnowledgeReuseResponse,
    ReportIngestRequest,
    ReportIngestResponse,
    SharingCatalogResponse,
    QualityMetricsResponse,
    RetrievalPerformanceResponse,
    EmbeddingManagementResponse,
    EmbeddingMigrateRequest,
    GovernanceReportResponse,
    AuditLogResponse,
    ConnectorConfigResponse,
    ImprovementSuggestionsResponse
)
from app.services.knowledge_intelligence import (
    knowledge_analytics_service,
    recommendation_service,
    knowledge_reuse_service,
    approved_knowledge_service,
    organization_knowledge_service,
    knowledge_quality_service,
    retrieval_analytics_service,
    embedding_management_service,
    governance_service,
    audit_service,
    connector_framework_service,
    continuous_improvement_service
)

router = APIRouter()

@router.get("/analytics", response_model=APIResponse[IntelligenceAnalyticsResponse])
async def get_intelligence_analytics(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """Get global knowledge growth, value, coverage, search, and reuse trends."""
    result = await knowledge_analytics_service.get_analytics(db)
    return success_response(data=result, message="Knowledge analytics compiled.")

@router.get("/recommendations", response_model=APIResponse[IntelligenceRecommendationResponse])
async def get_intelligence_recommendations(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """Generate recommendations for tags, gaps, missing knowledge, and related collections."""
    result = await recommendation_service.get_recommendations(db, UUID(user["id"]))
    return success_response(data=result, message="Actionable recommendations generated.")

@router.get("/reuse", response_model=APIResponse[KnowledgeReuseResponse])
async def get_knowledge_reuse(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """Retrieve cross-report evidence and chunk reuse metrics."""
    result = await knowledge_reuse_service.get_reuse_metrics(db)
    return success_response(data=result, message="Knowledge reuse report compiled.")

@router.post("/ingest-report", response_model=APIResponse[ReportIngestResponse], status_code=status.HTTP_201_CREATED)
async def ingest_approved_report(
    payload: ReportIngestRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """Ingest sections, blocks, and citations from an approved report into a collection."""
    result = await approved_knowledge_service.ingest_report(
        db=db,
        report_id=payload.report_id,
        target_collection_id=payload.target_collection_id,
        user_id=UUID(user["id"])
    )
    return success_response(data=result, message="Approved report ingested into collection.")

@router.get("/sharing", response_model=APIResponse[SharingCatalogResponse])
async def get_sharing_catalog(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """Retrieve catalog of collections shared organization-wide respecting permissions."""
    org_id = UUID(user["organization_id"]) if "organization_id" in user and user["organization_id"] else UUID("00000000-0000-0000-0000-000000000000")
    result = await organization_knowledge_service.get_sharing_catalog(db, org_id)
    return success_response(data=result, message="Shared collections catalog retrieved.")

@router.get("/quality", response_model=APIResponse[QualityMetricsResponse])
async def get_knowledge_quality(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """Compute overall quality scores based on authority, freshness, confidence, and validation state."""
    result = await knowledge_quality_service.get_quality_metrics(db)
    return success_response(data=result, message="Quality metrics evaluated.")

@router.get("/retrieval-performance", response_model=APIResponse[RetrievalPerformanceResponse])
async def get_retrieval_performance(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """Analyze retrieval accuracy, cache hit rate, and latency performance."""
    result = await retrieval_analytics_service.get_retrieval_performance(db)
    return success_response(data=result, message="Retrieval performance evaluated.")

@router.get("/embeddings", response_model=APIResponse[EmbeddingManagementResponse])
async def get_embedding_management(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """Retrieve state, performance, and count of active embedding dimensions."""
    result = await embedding_management_service.get_embedding_status(db)
    return success_response(data=result, message="Embedding system status retrieved.")

@router.post("/embeddings/migrate", response_model=APIResponse[dict])
async def migrate_embeddings(
    payload: EmbeddingMigrateRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """Triggers mock/logical embedding migration from a source model to a target model."""
    result = await embedding_management_service.migrate_embeddings(
        db=db,
        source_model=payload.source_model,
        target_model=payload.target_model,
        collection_id=payload.collection_id
    )
    return success_response(data=result, message="Embeddings migration complete.")

@router.get("/governance", response_model=APIResponse[GovernanceReportResponse])
async def get_knowledge_governance(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """Verify document compliance and flag documents exceeding retention thresholds."""
    result = await governance_service.get_governance_report(db)
    return success_response(data=result, message="Governance report compiled.")

@router.get("/audit", response_model=APIResponse[AuditLogResponse])
async def get_knowledge_audit(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """Retrieve historical logs for knowledge modifications, updates, and rollbacks."""
    result = await audit_service.get_audit_logs(db)
    return success_response(data=result, message="Audit logs retrieved.")

@router.get("/connectors", response_model=APIResponse[ConnectorConfigResponse])
async def get_connector_configs(
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """Retrieve configurable settings for extensible connector sources."""
    result = await connector_framework_service.get_connectors()
    return success_response(data=result, message="Connector framework configurations retrieved.")

@router.get("/improvements", response_model=APIResponse[ImprovementSuggestionsResponse])
async def get_improvement_suggestions(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """Suggest optimizations, identify stale content, and highlight duplicates."""
    result = await continuous_improvement_service.get_improvement_suggestions(db)
    return success_response(data=result, message="Continuous improvement plan compiled.")
