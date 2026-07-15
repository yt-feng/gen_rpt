from pydantic import BaseModel, Field, HttpUrl
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID

# ==========================================
# 1. Base Responses
# ==========================================
class KnowledgeBaseResponse(BaseModel):
    status: str = "success"
    message: str = "Success"

# ==========================================
# 2. Category Schemas
# ==========================================
class CategoryCreate(BaseModel):
    name: str = Field(..., max_length=255)
    slug: str = Field(..., max_length=255)
    parent_id: Optional[UUID] = None
    display_order: int = 0
    status: str = "active"
    description: Optional[str] = None

class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    parent_id: Optional[UUID] = None
    display_order: Optional[int] = None
    status: Optional[str] = None
    description: Optional[str] = None

class CategoryResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    parent_id: Optional[UUID]
    display_order: int
    status: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# ==========================================
# 3. Tag Schemas
# ==========================================
class TagCreate(BaseModel):
    name: str = Field(..., max_length=255)
    slug: str = Field(..., max_length=255)

class TagResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    created_at: datetime

    class Config:
        from_attributes = True

# ==========================================
# 4. Collection Permissions
# ==========================================
class PermissionCreate(BaseModel):
    user_id: UUID
    permission_level: str = Field(..., description="owner, editor, reviewer, viewer")

class PermissionResponse(BaseModel):
    id: UUID
    collection_id: UUID
    user_id: UUID
    permission_level: str
    created_at: datetime

    class Config:
        from_attributes = True

# ==========================================
# 5. Collection Schemas
# ==========================================
class CollectionCreate(BaseModel):
    name: str = Field(..., max_length=255)
    slug: str = Field(..., max_length=255)
    description: Optional[str] = None
    status: str = "active"
    owner_id: UUID
    organization_id: Optional[UUID] = None
    visibility: str = "private"

class CollectionUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    visibility: Optional[str] = None

class CollectionResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    description: Optional[str]
    status: str
    owner_id: UUID
    organization_id: Optional[UUID]
    visibility: str
    created_at: datetime
    updated_at: datetime
    tags: List[TagResponse] = []

    class Config:
        from_attributes = True

# ==========================================
# 6. Source Schemas
# ==========================================
class SourceCreate(BaseModel):
    publisher: Optional[str] = None
    author: Optional[str] = None
    url: Optional[str] = None
    publication_date: Optional[datetime] = None
    license: Optional[str] = None
    source_type: str = "manual_upload"
    authority_score: float = 1.0
    trust_score: float = 1.0
    region: Optional[str] = None
    language: Optional[str] = None

class SourceResponse(BaseModel):
    id: UUID
    document_id: Optional[UUID]
    publisher: Optional[str]
    author: Optional[str]
    url: Optional[str]
    publication_date: Optional[datetime]
    license: Optional[str]
    source_type: str
    authority_score: float
    trust_score: float
    region: Optional[str]
    language: Optional[str]

    class Config:
        from_attributes = True

# ==========================================
# 7. Document Schemas
# ==========================================
class DocumentCreate(BaseModel):
    collection_id: UUID
    file_name: str
    original_file_name: str
    mime_type: str
    extension: str
    checksum: str
    storage_path: str
    version: int = 1
    size: int
    language: Optional[str] = None
    page_count: Optional[int] = None
    processing_status: str = "pending"
    upload_status: str = "pending"
    validation_status: str = "pending"
    created_by: Optional[UUID] = None

class DocumentUpdate(BaseModel):
    file_name: Optional[str] = None
    processing_status: Optional[str] = None
    upload_status: Optional[str] = None
    validation_status: Optional[str] = None

class DocumentResponse(BaseModel):
    id: UUID
    collection_id: UUID
    file_name: str
    original_file_name: str
    mime_type: str
    extension: str
    checksum: str
    storage_path: str
    version: int
    size: int
    language: Optional[str]
    page_count: Optional[int]
    processing_status: str
    upload_status: str
    validation_status: str
    created_at: datetime
    tags: List[TagResponse] = []
    sources: List[SourceResponse] = []

    class Config:
        from_attributes = True

class DocumentVersionResponse(BaseModel):
    id: UUID
    document_id: UUID
    version_number: int
    parent_version_number: Optional[int]
    storage_path: str
    reason: Optional[str]
    created_by: Optional[UUID]
    created_at: datetime

    class Config:
        from_attributes = True

# ==========================================
# 8. Chunk & Embedding Schemas
# ==========================================
class ChunkCreate(BaseModel):
    document_id: UUID
    chunk_number: int
    section: Optional[str] = None
    heading: Optional[str] = None
    page: Optional[int] = None
    token_count: int = 0
    character_count: int = 0
    hash: Optional[str] = None
    processing_version: Optional[str] = None
    chunk_metadata: Optional[Dict[str, Any]] = None

class ChunkResponse(BaseModel):
    id: UUID
    document_id: UUID
    chunk_number: int
    section: Optional[str]
    heading: Optional[str]
    page: Optional[int]
    token_count: int
    character_count: int
    hash: Optional[str]
    processing_version: Optional[str]
    chunk_metadata: Optional[Dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True

class EmbeddingMetadataCreate(BaseModel):
    chunk_id: UUID
    embedding_model: str
    embedding_version: str
    dimension: int
    status: str = "pending"
    provider: Optional[str] = None

class EmbeddingMetadataResponse(BaseModel):
    id: UUID
    chunk_id: UUID
    embedding_model: str
    embedding_version: str
    dimension: int
    status: str
    generated_time: Optional[datetime]
    provider: Optional[str]
    latency: Optional[float]
    checksum: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

# ==========================================
# 9. Retrieval Session & Results Schemas
# ==========================================
class RetrievalRequest(BaseModel):
    topic: str
    target_count: int = 10
    collection_ids: Optional[List[UUID]] = None

class RetrievalSessionResponse(BaseModel):
    id: UUID
    query: str
    collection_id: Optional[UUID]
    user_id: Optional[UUID]
    generation_job_id: Optional[UUID]
    started_at: datetime
    completed_at: Optional[datetime]
    duration_ms: Optional[int]
    status: str

    class Config:
        from_attributes = True

class RetrievalResultResponse(BaseModel):
    id: UUID
    session_id: UUID
    chunk_id: UUID
    similarity_score: float
    ranking: int
    confidence: float
    source_id: Optional[UUID]
    result_metadata: Optional[Dict[str, Any]]

    class Config:
        from_attributes = True

# ==========================================
# 10. Validation & Relationships Schemas
# ==========================================
class ValidationRequest(BaseModel):
    session_id: Optional[UUID] = None
    document_id: Optional[UUID] = None
    validation_type: str
    confidence: float = 1.0
    result: str
    evidence: Optional[Dict[str, Any]] = None
    validator: Optional[str] = None

class ValidationResponse(BaseModel):
    id: UUID
    session_id: Optional[UUID]
    document_id: Optional[UUID]
    validation_type: str
    confidence: float
    result: str
    evidence: Optional[Dict[str, Any]]
    validator: Optional[str]
    execution_time_ms: Optional[int]
    summary: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

class KnowledgeRelationshipCreate(BaseModel):
    source_document_id: UUID
    target_document_id: UUID
    relationship_type: str
    relationship_metadata: Optional[Dict[str, Any]] = None

class KnowledgeRelationshipResponse(BaseModel):
    id: UUID
    source_document_id: UUID
    target_document_id: UUID
    relationship_type: str
    relationship_metadata: Optional[Dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True

# ==========================================
# 11. Queue & Audit Logs Schemas
# ==========================================
class ProcessingJobCreate(BaseModel):
    document_id: UUID
    priority: int = 0

class ProcessingJobResponse(BaseModel):
    id: UUID
    document_id: UUID
    status: str
    priority: int
    worker: Optional[str]
    attempts: int
    max_attempts: int
    logs: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class ProcessingAuditLogResponse(BaseModel):
    id: UUID
    job_id: Optional[UUID]
    worker: str
    stage: str
    duration_ms: int
    errors: Optional[str]
    retries: int
    outputs: Optional[Dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True

# ==========================================
# 12. History, Sync & Analytics Schemas
# ==========================================
class ActivityHistoryResponse(BaseModel):
    id: UUID
    collection_id: Optional[UUID]
    document_id: Optional[UUID]
    user_id: Optional[UUID]
    activity_type: str
    details: Optional[Dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True

class SynchronizationLogResponse(BaseModel):
    id: UUID
    entity_type: str
    status: str
    details: Optional[Dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True

class AnalyticsResponse(BaseModel):
    id: UUID
    collection_id: Optional[UUID]
    document_count: int
    chunk_count: int
    processing_count: int
    retrieval_count: int
    generation_count: int
    usage_metrics: Optional[Dict[str, Any]]
    recorded_date: datetime

    class Config:
        from_attributes = True
