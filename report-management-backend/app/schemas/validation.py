from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID

# ==========================================
# 1. Validation Policy Schemas
# ==========================================
class ValidationPolicyBase(BaseModel):
    name: str = Field(..., max_length=255)
    is_active: bool = True
    min_authority: float = Field(0.5, ge=0.0, le=1.0)
    min_freshness: float = Field(0.5, ge=0.0, le=1.0)
    min_confidence: float = Field(0.5, ge=0.0, le=1.0)
    max_duplicate_ratio: float = Field(0.3, ge=0.0, le=1.0)
    min_sources: int = Field(2, ge=1)
    conflict_threshold: float = Field(0.5, ge=0.0, le=1.0)
    knowledge_quality_threshold: float = Field(0.5, ge=0.0, le=1.0)
    rules: Optional[Dict[str, Any]] = None

class ValidationPolicyCreate(ValidationPolicyBase):
    pass

class ValidationPolicyUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    min_authority: Optional[float] = None
    min_freshness: Optional[float] = None
    min_confidence: Optional[float] = None
    max_duplicate_ratio: Optional[float] = None
    min_sources: Optional[int] = None
    conflict_threshold: Optional[float] = None
    knowledge_quality_threshold: Optional[float] = None
    rules: Optional[Dict[str, Any]] = None

class ValidationPolicyResponse(ValidationPolicyBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

# ==========================================
# 2. Validation Report Schemas
# ==========================================
class ValidationReportResponse(BaseModel):
    id: UUID
    session_id: Optional[UUID]
    knowledge_snapshot: Optional[Dict[str, Any]]
    retrieved_sources: Optional[Dict[str, Any]]
    validation_summary: Optional[str]
    authority_scores: Optional[Dict[str, Any]]
    freshness_scores: Optional[Dict[str, Any]]
    confidence_scores: Optional[Dict[str, Any]]
    conflicts: Optional[Dict[str, Any]]
    duplicate_analysis: Optional[Dict[str, Any]]
    evidence_completeness: Optional[Dict[str, Any]]
    unsupported_evidence: Optional[Dict[str, Any]]
    recommendations: Optional[Dict[str, Any]]
    r2_path: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# ==========================================
# 3. Validation History Schemas
# ==========================================
class ValidationHistoryResponse(BaseModel):
    id: UUID
    session_id: Optional[UUID]
    validation_run_id: UUID
    knowledge_version: str
    validation_policy_id: Optional[UUID]
    confidence_score: float
    conflict_count: int
    freshness_score: float
    details: Optional[Dict[str, Any]]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# ==========================================
# 4. Validation Audit Schemas
# ==========================================
class ValidationAuditResponse(BaseModel):
    id: UUID
    validator_version: str
    execution_time_ms: int
    knowledge_snapshot: Optional[Dict[str, Any]]
    retrieved_chunks: Optional[Dict[str, Any]]
    validation_rules: Optional[Dict[str, Any]]
    results: Optional[Dict[str, Any]]
    warnings: Optional[Dict[str, Any]]
    errors: Optional[Dict[str, Any]]
    user_id: Optional[UUID]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# ==========================================
# 5. Validated Context Package
# ==========================================
class ValidatedChunkSchema(BaseModel):
    chunk_id: UUID
    document_id: UUID
    text: str
    confidence: float
    authority: float
    is_duplicate: bool
    conflicts_with: List[UUID] = []
    validation_status: str  # validated, flagged, conflict, duplicate
    metadata: Optional[Dict[str, Any]] = None

class ValidatedSourceSchema(BaseModel):
    source_id: UUID
    document_id: UUID
    publisher: Optional[str]
    source_type: str
    authority_score: float
    freshness_score: float
    validation_status: str

class ValidatedContextPackage(BaseModel):
    validated_chunks: List[ValidatedChunkSchema]
    validated_sources: List[ValidatedSourceSchema]
    confidence_scores: Dict[str, Any]  # overall, per_source, per_chunk, per_claim
    authority_scores: Dict[str, float]
    evidence_ranking: List[UUID]  # ranked list of chunk_ids
    knowledge_snapshot: Dict[str, Any]
    validation_report_reference: UUID
    collection_metadata: Dict[str, Any]
    document_references: List[Dict[str, Any]]
    context_metadata: Dict[str, Any]
