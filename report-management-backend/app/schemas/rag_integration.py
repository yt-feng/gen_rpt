from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID

class KnowledgeSnapshotBase(BaseModel):
    knowledge_version: str
    collections_used: Optional[List[UUID]] = None
    documents_used: Optional[List[UUID]] = None
    chunks_used: Optional[List[UUID]] = None
    embedding_version: str = "1.0"
    validation_version: str = "1.0"
    retrieval_session_id: Optional[UUID] = None
    configuration: Optional[Dict[str, Any]] = None
    r2_path: Optional[str] = None

class KnowledgeSnapshotCreate(KnowledgeSnapshotBase):
    pass

class KnowledgeSnapshotResponse(KnowledgeSnapshotBase):
    id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EvidenceAttributionBase(BaseModel):
    generation_job_id: Optional[UUID] = None
    section_id: Optional[str] = None
    supporting_chunks: Optional[List[UUID]] = None
    supporting_documents: Optional[List[UUID]] = None
    supporting_sources: Optional[List[UUID]] = None
    supporting_collections: Optional[List[UUID]] = None
    confidence: float = 1.0
    validation_report_id: Optional[UUID] = None
    snapshot_id: Optional[UUID] = None

class EvidenceAttributionCreate(EvidenceAttributionBase):
    pass

class EvidenceAttributionResponse(EvidenceAttributionBase):
    id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GenerationAnalyticsBase(BaseModel):
    generation_job_id: Optional[UUID] = None
    collections_used: Optional[List[UUID]] = None
    retrieved_documents_count: int = 0
    retrieved_chunks_count: int = 0
    context_size: int = 0
    cache_hit: bool = False
    retrieval_time_ms: int = 0
    validation_time_ms: int = 0
    prompt_build_time_ms: int = 0
    generation_time_ms: int = 0
    knowledge_reuse_metrics: Optional[Dict[str, Any]] = None
    evidence_usage_metrics: Optional[Dict[str, Any]] = None

class GenerationAnalyticsCreate(GenerationAnalyticsBase):
    pass

class GenerationAnalyticsResponse(GenerationAnalyticsBase):
    id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GenerationContextCacheBase(BaseModel):
    cache_key: str
    context_package: Optional[Dict[str, Any]] = None
    expires_at: datetime

class GenerationContextCacheResponse(GenerationContextCacheBase):
    id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
