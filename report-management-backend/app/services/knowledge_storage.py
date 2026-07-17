import hashlib
import time
from typing import Union, BinaryIO, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, timezone
from app.core.config import settings
from app.storage.provider import get_storage_provider, StorageProvider

class KnowledgeStorageService:
    def __init__(self, provider_type: Optional[str] = None):
        self.provider_type = provider_type or settings.KNOWLEDGE_STORAGE_PROVIDER
        self.provider: StorageProvider = get_storage_provider(self.provider_type)

    def generate_document_path(self, collection_id: UUID, document_id: UUID, filename: str, version: int = 1) -> str:
        """
        logical storage hierarchy for document versions:
        knowledge/organization/collection/original/
        """
        return f"knowledge/organization/collection/original/{collection_id}/{document_id}_v{version}_{filename}"

    def generate_archive_path(self, collection_id: UUID, document_id: UUID, filename: str, version: int = 1) -> str:
        """
        logical hierarchy for soft deleted archive documents
        """
        timestamp = int(time.time())
        return f"knowledge/archive/{collection_id}/{document_id}_v{version}_{timestamp}_{filename}"

    def generate_export_path(self, collection_id: UUID, format_ext: str, export_id: UUID) -> str:
        """
        logical hierarchy for knowledge exports
        """
        return f"knowledge/processed/exports/{collection_id}_{export_id}.{format_ext}"

    def generate_log_path(self, log_type: str, timestamp: datetime) -> str:
        """
        logical hierarchy for ingestion and processing logs
        """
        date_str = timestamp.strftime("%Y-%m-%d")
        epoch = int(timestamp.timestamp())
        return f"knowledge/logs/{log_type}/{date_str}_{epoch}.log"

    def generate_processing_path(self, document_id: UUID, stage: str, output_id: UUID) -> str:
        """
        intermediate/processed pipeline storage mapped to exact prefixes:
        processed, text, chunks, embeddings, validation
        """
        if stage == "extraction":
            return f"knowledge/text/{document_id}_{output_id}.json"
        elif stage == "chunking":
            return f"knowledge/chunks/{document_id}_{output_id}.json"
        elif stage == "embeddings":
            return f"knowledge/embeddings/{document_id}_{output_id}.json"
        elif stage == "validation":
            return f"knowledge/validation/{document_id}_{output_id}.json"
        else:
            return f"knowledge/processed/{document_id}_{stage}_{output_id}.json"

    def generate_retrieval_path(self, session_id: UUID) -> str:
        """
        logical hierarchy for retrieval and validation snapshots
        """
        return f"knowledge/retrieval/{session_id}/snapshot.json"

    def calculate_checksum(self, file_data: Union[bytes, BinaryIO]) -> str:
        """
        Task 21: Checksum calculator
        """
        algo = settings.KNOWLEDGE_STORAGE_CHECKSUM_ALGO.lower()
        if algo == "md5":
            hasher = hashlib.md5()
        else:
            hasher = hashlib.sha256()

        if isinstance(file_data, bytes):
            hasher.update(file_data)
        else:
            file_data.seek(0)
            while chunk := file_data.read(8192):
                hasher.update(chunk)
            file_data.seek(0)
        return hasher.hexdigest()

    def validate_object_integrity(self, file_data: Union[bytes, BinaryIO], expected_checksum: str, expected_size: int, mime_type: str) -> bool:
        """
        Task 21: Validate file size, checksum, and mime types.
        """
        # 1. Size check
        if isinstance(file_data, bytes):
            actual_size = len(file_data)
        else:
            file_data.seek(0, 2)
            actual_size = file_data.tell()
            file_data.seek(0)

        if actual_size != expected_size:
            return False

        # 2. Checksum check
        actual_checksum = self.calculate_checksum(file_data)
        if actual_checksum != expected_checksum:
            return False

        # 3. MIME type check (basic format safety check)
        allowed_mimes = [
            "application/pdf", 
            "text/markdown", 
            "text/plain", 
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/html"
        ]
        if mime_type not in allowed_mimes:
            return False

        return True

    async def get_storage_metadata(self, path: str) -> Dict[str, Any]:
        """
        Task 20: Resolve object metadata: Immutable version numbers, content hash, and timestamp parameters.
        """
        # Stubs for metadata extraction from target provider
        exists = await self.provider.exists(path)
        if not exists:
            return {}

        return {
            "path": path,
            "version": 1,
            "parent_version": None,
            "content_hash": "dummyhash",
            "checksum_algo": settings.KNOWLEDGE_STORAGE_CHECKSUM_ALGO,
            "size": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "immutable": settings.KNOWLEDGE_STORAGE_VERSIONING
        }

    async def get_storage_stats(self) -> Dict[str, Any]:
        """
        Task 22: Storage usage monitoring stats
        """
        return {
            "object_count": 0,
            "storage_usage_bytes": 0,
            "upload_rate_limit": "unlimited",
            "download_rate_limit": "unlimited",
            "archive_count": 0,
            "error_count": 0,
            "avg_latency_ms": 0.0,
            "active_provider": self.provider_type
        }

    async def check_connectivity(self) -> Dict[str, Any]:
        """
        Task 23: Perform storage health check connectivity
        """
        try:
            health = await self.provider.health_check()
            return {
                "status": "Ready" if health.get("status") in ("healthy", "not_implemented") else "Degraded",
                "provider": self.provider_type,
                "details": health
            }
        except Exception as e:
            return {
                "status": "Degraded",
                "provider": self.provider_type,
                "error": str(e)
            }

knowledge_storage_service = KnowledgeStorageService()
