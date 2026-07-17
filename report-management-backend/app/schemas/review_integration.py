import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict

class ReviewSnapshotBase(BaseModel):
    version_id: uuid.UUID
    knowledge_snapshot_id: Optional[uuid.UUID] = None
    validation_report_id: Optional[uuid.UUID] = None
    ai_review_id: Optional[uuid.UUID] = None
    human_review_id: Optional[uuid.UUID] = None
    evidence_attribution_id: Optional[uuid.UUID] = None
    review_results: Optional[Dict[str, Any]] = None
    r2_path: Optional[str] = None
    reviewer_id: Optional[uuid.UUID] = None

class ReviewSnapshotCreate(ReviewSnapshotBase):
    pass

class ReviewSnapshotResponse(ReviewSnapshotBase):
    id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReviewAnalyticsBase(BaseModel):
    review_snapshot_id: Optional[uuid.UUID] = None
    evidence_usage: Optional[Dict[str, Any]] = None
    citation_quality: Optional[Dict[str, Any]] = None
    confidence_score: float = 1.0
    unsupported_claims_count: int = 0
    conflicts_count: int = 0
    review_duration_seconds: int = 0

class ReviewAnalyticsCreate(ReviewAnalyticsBase):
    pass

class ReviewAnalyticsResponse(ReviewAnalyticsBase):
    id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Supporting Schemas for API responses ---

class EvidenceChunkInfo(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    collection_id: uuid.UUID
    collection_name: str
    content: str
    similarity_score: float
    authority_score: float
    freshness_score: float
    confidence_score: float

class EvidenceViewerData(BaseModel):
    version_id: uuid.UUID
    supporting_chunks: List[EvidenceChunkInfo]
    validation_summary: Dict[str, Any]
    metadata: Dict[str, Any]

class TraceabilityNode(BaseModel):
    node_stable_id: str
    node_text: str
    supporting_chunk_ids: List[uuid.UUID]
    supporting_document_ids: List[uuid.UUID]
    confidence: float
    validation_status: str

class CitationValidationInfo(BaseModel):
    citation_text: str
    referenced_source_exists: bool
    referenced_document_exists: bool
    matches_evidence: bool
    freshness: float
    authority: float
    status: str  # e.g., "valid", "broken", "expired", "missing"

class CitationVerificationSummary(BaseModel):
    version_id: uuid.UUID
    citations: List[CitationValidationInfo]
    broken_count: int
    missing_count: int
    expired_count: int

class UnsupportedClaimInfo(BaseModel):
    statement: str
    location: str  # e.g. section, paragraph
    confidence: float
    issue_type: str  # e.g., "unsupported", "weak_claim", "conflict", "unvalidated"
    description: str

class ValidationDashboardData(BaseModel):
    validation_summary: Dict[str, Any]
    evidence_coverage: float
    authority_distribution: Dict[str, float]
    confidence_distribution: Dict[str, float]
    unsupported_claims_count: int
    conflicts_count: int
    duplicate_evidence_ratio: float
    freshness_summary: Dict[str, Any]
