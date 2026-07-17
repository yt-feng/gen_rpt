import uuid
from datetime import datetime
from sqlalchemy import (
    String,
    Integer,
    Float,
    BigInteger,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
    Table,
    Column,
    func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector
from app.models.base import Base, UUIDMixin, TimestampMixin, SoftDeleteMixin, JSONVariant

# Many-to-Many association table: Collections <-> Tags
knowledge_collection_tags = Table(
    "knowledge_collection_tags",
    Base.metadata,
    Column("collection_id", UUID(as_uuid=True), ForeignKey("knowledge_collections.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", UUID(as_uuid=True), ForeignKey("knowledge_tags.id", ondelete="CASCADE"), primary_key=True),
)

# Many-to-Many association table: Documents <-> Tags
knowledge_document_tags = Table(
    "knowledge_document_tags",
    Base.metadata,
    Column("document_id", UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", UUID(as_uuid=True), ForeignKey("knowledge_tags.id", ondelete="CASCADE"), primary_key=True),
)


class KnowledgeCollection(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "knowledge_collections"

    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active") # active, archived
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    visibility: Mapped[str] = mapped_column(String, default="private") # private, shared, public
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    documents: Mapped[list["KnowledgeDocument"]] = relationship("KnowledgeDocument", back_populates="collection", cascade="all, delete-orphan")
    tags: Mapped[list["KnowledgeTag"]] = relationship("KnowledgeTag", secondary=knowledge_collection_tags, back_populates="collections")
    permissions: Mapped[list["CollectionPermission"]] = relationship("CollectionPermission", back_populates="collection", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_knowledge_collections_owner_id", "owner_id"),
        Index("ix_knowledge_collections_status", "status"),
    )


class KnowledgeDocument(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "knowledge_documents"

    collection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_collections.id", ondelete="CASCADE"), nullable=False)
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    original_file_name: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    extension: Mapped[str] = mapped_column(String, nullable=False)
    checksum: Mapped[str] = mapped_column(String, nullable=False)
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processing_status: Mapped[str] = mapped_column(String, default="pending") # pending, processing, completed, failed
    upload_status: Mapped[str] = mapped_column(String, default="pending") # pending, uploaded, failed
    validation_status: Mapped[str] = mapped_column(String, default="pending") # pending, validated, failed
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    collection: Mapped[KnowledgeCollection] = relationship("KnowledgeCollection", back_populates="documents")
    tags: Mapped[list["KnowledgeTag"]] = relationship("KnowledgeTag", secondary=knowledge_document_tags, back_populates="documents")
    sources: Mapped[list["KnowledgeSource"]] = relationship("KnowledgeSource", back_populates="document", cascade="all, delete-orphan")
    chunks: Mapped[list["KnowledgeChunk"]] = relationship("KnowledgeChunk", back_populates="document", cascade="all, delete-orphan")
    versions: Mapped[list["KnowledgeVersionHistory"]] = relationship("KnowledgeVersionHistory", back_populates="document", cascade="all, delete-orphan")
    queue_jobs: Mapped[list["KnowledgeProcessingQueue"]] = relationship("KnowledgeProcessingQueue", back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_knowledge_documents_collection_id", "collection_id"),
        Index("ix_knowledge_documents_checksum", "checksum"),
        Index("ix_knowledge_documents_processing_status", "processing_status"),
    )


class KnowledgeSource(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "knowledge_sources"

    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String, nullable=True)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    license: Mapped[str | None] = mapped_column(String, nullable=True)
    source_type: Mapped[str] = mapped_column(String, default="manual_upload") # internal, external, government, research, company, manual_upload, generated
    authority_score: Mapped[float] = mapped_column(Float, default=1.0)
    trust_score: Mapped[float] = mapped_column(Float, default=1.0)
    region: Mapped[str | None] = mapped_column(String, nullable=True)
    language: Mapped[str | None] = mapped_column(String, nullable=True)

    # Relationships
    document: Mapped[KnowledgeDocument | None] = relationship("KnowledgeDocument", back_populates="sources")

    __table_args__ = (
        Index("ix_knowledge_sources_document_id", "document_id"),
        Index("ix_knowledge_sources_source_type", "source_type"),
    )


class KnowledgeCategory(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "knowledge_categories"

    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_categories.id", ondelete="SET NULL"), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="active")
    description: Mapped[str | None] = mapped_column(String, nullable=True)

    # Relationships
    parent = relationship("KnowledgeCategory", remote_side="KnowledgeCategory.id", backref="children")

    __table_args__ = (
        Index("ix_knowledge_categories_parent_id", "parent_id"),
        Index("ix_knowledge_categories_slug", "slug"),
    )


class KnowledgeTag(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "knowledge_tags"

    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    # Relationships
    collections: Mapped[list[KnowledgeCollection]] = relationship("KnowledgeCollection", secondary=knowledge_collection_tags, back_populates="tags")
    documents: Mapped[list[KnowledgeDocument]] = relationship("KnowledgeDocument", secondary=knowledge_document_tags, back_populates="tags")

    __table_args__ = (
        Index("ix_knowledge_tags_slug", "slug"),
    )


class KnowledgeChunk(Base, UUIDMixin):
    __tablename__ = "knowledge_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False)
    chunk_number: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String, nullable=True)
    heading: Mapped[str | None] = mapped_column(String, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    character_count: Mapped[int] = mapped_column(Integer, default=0)
    hash: Mapped[str | None] = mapped_column(String, nullable=True)
    processing_version: Mapped[str | None] = mapped_column(String, nullable=True)
    chunk_metadata: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    # Relationships
    document: Mapped[KnowledgeDocument] = relationship("KnowledgeDocument", back_populates="chunks")
    embeddings: Mapped[list["EmbeddingMetadata"]] = relationship("EmbeddingMetadata", back_populates="chunk", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_knowledge_chunks_document_id", "document_id"),
        Index("ix_knowledge_chunks_chunk_number", "chunk_number"),
    )


class EmbeddingMetadata(Base, UUIDMixin):
    __tablename__ = "embedding_metadata"

    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String, nullable=False)
    embedding_version: Mapped[str] = mapped_column(String, nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending") # pending, completed, failed
    generated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    latency: Mapped[float | None] = mapped_column(Float, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    # Relationships
    chunk: Mapped[KnowledgeChunk] = relationship("KnowledgeChunk", back_populates="embeddings")

    __table_args__ = (
        Index("ix_embedding_metadata_chunk_id", "chunk_id"),
    )


class RetrievalSession(Base, UUIDMixin):
    __tablename__ = "retrieval_sessions"

    query: Mapped[str] = mapped_column(String, nullable=False)
    collection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_collections.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    generation_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending") # pending, completed, failed
    request_metadata: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    snapshot_metadata: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    session_metadata: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)

    __table_args__ = (
        Index("ix_retrieval_sessions_collection_id", "collection_id"),
        Index("ix_retrieval_sessions_user_id", "user_id"),
    )


class RetrievalResult(Base, UUIDMixin):
    __tablename__ = "retrieval_results"

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("retrieval_sessions.id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), nullable=False)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    ranking: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_sources.id", ondelete="SET NULL"), nullable=True)
    result_metadata: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)

    __table_args__ = (
        Index("ix_retrieval_results_session_id", "session_id"),
    )


class ValidationResult(Base, UUIDMixin):
    __tablename__ = "validation_results"

    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("retrieval_sessions.id", ondelete="CASCADE"), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=True)
    validation_type: Mapped[str] = mapped_column(String, nullable=False) # source_validation, evidence_validation, duplicate_validation, etc.
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    result: Mapped[str] = mapped_column(String, nullable=False) # validated, flagged, conflict, duplicate
    evidence: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    validator: Mapped[str | None] = mapped_column(String, nullable=True)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("ix_validation_results_session_id", "session_id"),
        Index("ix_validation_results_document_id", "document_id"),
    )


class KnowledgeRelationship(Base, UUIDMixin):
    __tablename__ = "knowledge_relationships"

    source_document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False)
    target_document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String, nullable=False) # references, duplicate_of, derived_from, related_to, parent_of, child_of, citation
    relationship_metadata: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("ix_knowledge_relationships_source_document_id", "source_document_id"),
        Index("ix_knowledge_relationships_target_document_id", "target_document_id"),
    )


class KnowledgeProcessingQueue(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "knowledge_processing_queue"

    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending") # pending, running, completed, failed, retry, cancelled
    priority: Mapped[int] = mapped_column(Integer, default=0)
    worker: Mapped[str | None] = mapped_column(String, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    logs: Mapped[str | None] = mapped_column(String, nullable=True)

    # Relationships
    document: Mapped[KnowledgeDocument] = relationship("KnowledgeDocument", back_populates="queue_jobs")
    audit_logs: Mapped[list["KnowledgeProcessingAuditLog"]] = relationship("KnowledgeProcessingAuditLog", back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_knowledge_processing_queue_document_id", "document_id"),
        Index("ix_knowledge_processing_queue_status", "status"),
    )


class KnowledgeActivityHistory(Base, UUIDMixin):
    __tablename__ = "knowledge_activity_history"

    collection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_collections.id", ondelete="SET NULL"), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    activity_type: Mapped[str] = mapped_column(String, nullable=False) # upload, delete, move, rename, validation, processing, collection_change, permission_change
    details: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("ix_knowledge_activity_history_collection_id", "collection_id"),
        Index("ix_knowledge_activity_history_document_id", "document_id"),
    )


class CollectionPermission(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "collection_permissions"

    collection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_collections.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    permission_level: Mapped[str] = mapped_column(String, nullable=False) # owner, editor, reviewer, viewer

    # Relationships
    collection: Mapped[KnowledgeCollection] = relationship("KnowledgeCollection", back_populates="permissions")

    __table_args__ = (
        UniqueConstraint("collection_id", "user_id", name="uq_collection_user_permission"),
        Index("ix_collection_permissions_collection_id", "collection_id"),
    )


class KnowledgeAnalytics(Base, UUIDMixin):
    __tablename__ = "knowledge_analytics"

    collection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_collections.id", ondelete="CASCADE"), nullable=True)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    processing_count: Mapped[int] = mapped_column(Integer, default=0)
    retrieval_count: Mapped[int] = mapped_column(Integer, default=0)
    generation_count: Mapped[int] = mapped_column(Integer, default=0)
    usage_metrics: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    recorded_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("ix_knowledge_analytics_recorded_date", "recorded_date"),
    )


class KnowledgeVersionHistory(Base, UUIDMixin):
    __tablename__ = "knowledge_version_history"

    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    # Relationships
    document: Mapped[KnowledgeDocument] = relationship("KnowledgeDocument", back_populates="versions")

    __table_args__ = (
        Index("ix_knowledge_version_history_document_id", "document_id"),
    )


class KnowledgeSynchronizationLog(Base, UUIDMixin):
    __tablename__ = "knowledge_synchronization_logs"

    entity_type: Mapped[str] = mapped_column(String, nullable=False) # r2, processing, embedding, retrieval, generation, review, publishing
    status: Mapped[str] = mapped_column(String, nullable=False) # success, failure, pending
    details: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("ix_knowledge_synchronization_logs_created_at", "created_at"),
    )


class KnowledgeProcessingAuditLog(Base, UUIDMixin):
    __tablename__ = "knowledge_processing_audit_logs"

    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_processing_queue.id", ondelete="CASCADE"), nullable=True)
    worker: Mapped[str] = mapped_column(String, nullable=False)
    stage: Mapped[str] = mapped_column(String, nullable=False) # text_extraction, chunking, embedding, validation
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    errors: Mapped[str | None] = mapped_column(String, nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    outputs: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    # Relationships
    job: Mapped[KnowledgeProcessingQueue | None] = relationship("KnowledgeProcessingQueue", back_populates="audit_logs")

    __table_args__ = (
        Index("ix_knowledge_processing_audit_logs_job_id", "job_id"),
    )
