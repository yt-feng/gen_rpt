from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID

# --- Base Schema Response ---
class KnowledgeBaseResponse(BaseModel):
    status: str = "success"
    message: str = "Placeholder response"

# --- Collections Schemas ---
class CollectionCreate(BaseModel):
    name: str = Field(..., max_length=255, description="Name of the collection")
    description: Optional[str] = Field(None, description="Description of the collection")

class CollectionResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

# --- Documents Schemas ---
class DocumentResponse(BaseModel):
    id: UUID
    collection_id: UUID
    file_name: str
    file_type: str
    file_size_bytes: int
    processing_status: str
    created_at: datetime

# --- Search and Retrieval Schemas ---
class SearchRequest(BaseModel):
    query: str = Field(..., description="The query string to search for")
    collection_ids: Optional[List[UUID]] = Field(None, description="Filter by collections")
    tags: Optional[List[str]] = Field(None, description="Filter by tags")
    limit: int = Field(10, ge=1, le=100)

class SearchResultItem(BaseModel):
    document_id: UUID
    file_name: str
    chunk_index: int
    content: str
    score: float
    page_number: Optional[int] = None
    metadata: Dict[str, Any] = {}

class SearchResponse(BaseModel):
    query: str
    results: List[SearchResultItem]
    total_results: int

class RetrievalRequest(BaseModel):
    topic: str
    target_count: int = 10
    collection_ids: Optional[List[UUID]] = None

class ChunkReference(BaseModel):
    chunk_id: UUID
    document_id: UUID
    source_name: str
    content: str
    score: float
    page_number: Optional[int] = None

class RetrievalResponse(BaseModel):
    session_id: UUID
    retrieved_chunks: List[ChunkReference]
    context_package_id: UUID

# --- Validation Schemas ---
class ValidationRequest(BaseModel):
    chunks: List[ChunkReference]

class ValidationResultItem(BaseModel):
    chunk_id: UUID
    status: str = "validated"  # e.g., validated, flagged, conflict, duplicate
    score: float = 1.0
    reason: Optional[str] = None

class ValidationResponse(BaseModel):
    is_valid: bool
    results: List[ValidationResultItem]
    confidence_score: float

# --- Analytics & Admin Schemas ---
class AnalyticsResponse(BaseModel):
    total_collections: int = 0
    total_documents: int = 0
    total_chunks: int = 0
    total_queries: int = 0
    average_response_time_ms: float = 0.0

class AdminStatusResponse(BaseModel):
    status: str = "idle"
    workers_active: int = 0
    jobs_in_queue: int = 0
    configuration_status: str = "valid"
    feature_flags: Dict[str, bool] = {}

# --- Health Schemas ---
class KnowledgeHealthResponse(BaseModel):
    status: str = "healthy"
    module_loaded: bool = True
    workers_status: str = "idle"
    queue_status: str = "idle"
    processing_status: str = "idle"
    storage_provider: str
    vector_provider: str
    feature_flags: Dict[str, bool]
