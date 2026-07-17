from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID

class IntelligenceAnalyticsResponse(BaseModel):
    growth_metrics: Dict[str, Any]
    usage_metrics: Dict[str, Any]
    coverage_metrics: Dict[str, Any]
    search_trends: List[Dict[str, Any]]
    retrieval_trends: List[Dict[str, Any]]
    value_metrics: Dict[str, Any]
    reuse_metrics: Dict[str, Any]
    quality_metrics: Dict[str, Any]

class IntelligenceRecommendationResponse(BaseModel):
    related_documents: List[Dict[str, Any]]
    related_collections: List[Dict[str, Any]]
    missing_knowledge: List[Dict[str, Any]]
    suggested_sources: List[Dict[str, Any]]
    suggested_tags: List[Dict[str, Any]]
    suggested_categories: List[Dict[str, Any]]
    knowledge_gaps: List[Dict[str, Any]]
    relevant_collections: List[Dict[str, Any]]
    knowledge_improvements: List[Dict[str, Any]]

class KnowledgeReuseResponse(BaseModel):
    shared_evidence_count: int
    shared_references_count: int
    shared_citations_count: int
    shared_chunks_count: int
    shared_documents_count: int
    lineage: Dict[str, Any]
    reuse_statistics: Dict[str, Any]

class ReportIngestRequest(BaseModel):
    report_id: UUID
    target_collection_id: UUID

class ReportIngestResponse(BaseModel):
    status: str
    document_id: UUID
    chunks_count: int
    validation_status: str

class SharingCatalogResponse(BaseModel):
    shared_collections: List[Dict[str, Any]]
    organization_catalog: List[Dict[str, Any]]

class QualityMetricsResponse(BaseModel):
    overall_quality_score: float
    authority_score: float
    freshness_score: float
    coverage_score: float
    confidence_score: float
    validation_score: float
    evidence_quality_score: float
    completeness_score: float
    effectiveness_score: float
    health_status: str

class RetrievalPerformanceResponse(BaseModel):
    average_similarity: float
    average_confidence: float
    average_latency_ms: float
    cache_hit_rate: float
    coverage_score: float
    evidence_usage_rate: float
    search_accuracy: float
    validation_success_rate: float
    top_collections: List[Dict[str, Any]]
    top_documents: List[Dict[str, Any]]
    top_chunks: List[Dict[str, Any]]
    optimization_recommendations: List[str]

class EmbeddingModelInfo(BaseModel):
    model_name: str
    version: str
    dimension: int
    provider: str
    is_active: bool

class EmbeddingManagementResponse(BaseModel):
    models: List[EmbeddingModelInfo]
    total_embeddings_count: int
    unembedded_chunks_count: int
    health_status: str

class EmbeddingMigrateRequest(BaseModel):
    source_model: str
    target_model: str
    collection_id: Optional[UUID] = None

class GovernanceReportResponse(BaseModel):
    policy_compliance_rate: float
    retention_flagged_count: int
    non_compliant_documents: List[Dict[str, Any]]
    policies_active: List[Dict[str, Any]]

class AuditLogResponse(BaseModel):
    logs: List[Dict[str, Any]]

class ConnectorConfigResponse(BaseModel):
    connectors: List[Dict[str, Any]]

class ImprovementSuggestionsResponse(BaseModel):
    stale_documents: List[Dict[str, Any]]
    duplicate_documents: List[Dict[str, Any]]
    gap_documents: List[Dict[str, Any]]
    suggestions: List[Dict[str, Any]]
