"""
Knowledge Models Skeleton (Phase R1)
These are architectural declarations. Database tables will be implemented in Phase R2.
"""

from typing import Dict, Any

class KnowledgeCollectionModelPlaceholder:
    """
    Proposed Schema for KnowledgeCollection:
    - id: UUID (PK)
    - name: String (Unique)
    - description: String (Nullable)
    - created_by: UUID (FK to User)
    - created_at: DateTime
    - updated_at: DateTime
    """
    pass

class KnowledgeDocumentModelPlaceholder:
    """
    Proposed Schema for KnowledgeDocument:
    - id: UUID (PK)
    - collection_id: UUID (FK to KnowledgeCollection)
    - file_name: String
    - storage_path: String (R2 Key)
    - file_type: String (PDF, MD, DOCX, TXT, HTML)
    - checksum_sha256: String
    - file_size_bytes: BigInteger
    - processing_status: Enum (pending, processing, completed, failed)
    - owner_id: UUID (FK to User)
    - created_at: DateTime
    """
    pass

class KnowledgeChunkModelPlaceholder:
    """
    Proposed Schema for KnowledgeChunk:
    - id: UUID (PK)
    - document_id: UUID (FK to KnowledgeDocument)
    - content: String (Text chunk content)
    - page_number: Integer (Nullable)
    - chunk_index: Integer
    - embedding_id: UUID (Nullable, reference to pgvector store)
    - metadata: JSONB
    - created_at: DateTime
    """
    pass
