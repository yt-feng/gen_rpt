import pytest
import io
from uuid import uuid4
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.core.config import settings
from app.storage.provider import (
    get_storage_provider, 
    CloudflareR2Provider, 
    S3StorageProvider, 
    GoogleCloudStorageProvider, 
    AzureBlobStorageProvider, 
    MinIOStorageProvider
)
from app.services.knowledge_storage import KnowledgeStorageService, knowledge_storage_service
from app.main import app

client = TestClient(app)

def test_storage_provider_factory():
    # Factory checks
    assert isinstance(get_storage_provider("r2"), CloudflareR2Provider)
    assert isinstance(get_storage_provider("s3"), S3StorageProvider)
    assert isinstance(get_storage_provider("gcs"), GoogleCloudStorageProvider)
    assert isinstance(get_storage_provider("azure"), AzureBlobStorageProvider)
    assert isinstance(get_storage_provider("minio"), MinIOStorageProvider)

    with pytest.raises(ValueError):
        get_storage_provider("unknown_provider")

def test_storage_configurations():
    # Assert settings default load
    assert settings.KNOWLEDGE_STORAGE_PREFIX == "knowledge/"
    assert settings.KNOWLEDGE_ARCHIVE_PREFIX == "archive/"
    assert settings.KNOWLEDGE_EXPORT_PREFIX == "exports/"
    assert settings.KNOWLEDGE_LOG_PREFIX == "logs/"
    assert settings.KNOWLEDGE_PROCESSING_PREFIX == "processing/"
    assert settings.KNOWLEDGE_RETENTION_POLICY_DAYS == 30
    assert settings.KNOWLEDGE_STORAGE_VERSIONING is True
    assert settings.KNOWLEDGE_STORAGE_CHECKSUM_ALGO == "sha256"

def test_path_generation_schemas():
    collection_id = uuid4()
    document_id = uuid4()
    filename = "document.pdf"

    # Test doc path
    doc_path = knowledge_storage_service.generate_document_path(collection_id, document_id, filename, version=2)
    assert doc_path == f"knowledge/collections/{collection_id}/documents/{document_id}/v2/document.pdf"

    # Test archive path
    arch_path = knowledge_storage_service.generate_archive_path(collection_id, document_id, filename, version=1)
    assert arch_path.startswith(f"archive/collections/{collection_id}/documents/{document_id}/v1_")

    # Test export path
    export_id = uuid4()
    exp_path = knowledge_storage_service.generate_export_path(collection_id, "zip", export_id)
    assert exp_path == f"exports/collections/{collection_id}/exports/{export_id}.zip"

    # Test log path
    now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
    log_path = knowledge_storage_service.generate_log_path("ingestion", now)
    assert log_path == "logs/ingestion/2026-07-16/1781611200.log"

def test_checksum_and_validation():
    data = b"Testing content validation"
    size = len(data)
    
    # Calculate expected SHA256 checksum
    import hashlib
    hasher = hashlib.sha256()
    hasher.update(data)
    expected_checksum = hasher.hexdigest()

    # Valid check
    is_valid = knowledge_storage_service.validate_object_integrity(
        file_data=data,
        expected_checksum=expected_checksum,
        expected_size=size,
        mime_type="text/markdown"
    )
    assert is_valid is True

    # Bad size
    assert knowledge_storage_service.validate_object_integrity(
        file_data=data,
        expected_checksum=expected_checksum,
        expected_size=size + 1,
        mime_type="text/markdown"
    ) is False

    # Bad checksum
    assert knowledge_storage_service.validate_object_integrity(
        file_data=data,
        expected_checksum="badchecksum",
        expected_size=size,
        mime_type="text/markdown"
    ) is False

    # Bad MIME type
    assert knowledge_storage_service.validate_object_integrity(
        file_data=data,
        expected_checksum=expected_checksum,
        expected_size=size,
        mime_type="application/octet-stream"  # not in whitelist
    ) is False

@pytest.mark.asyncio
async def test_metadata_and_stats():
    metadata = await knowledge_storage_service.get_storage_metadata("non-existent-path")
    assert metadata == {}  # empty because path does not exist

    stats = await knowledge_storage_service.get_storage_stats()
    assert stats["active_provider"] == settings.KNOWLEDGE_STORAGE_PROVIDER
    assert stats["object_count"] == 0

@pytest.mark.asyncio
async def test_health_check_connectivity():
    connectivity = await knowledge_storage_service.check_connectivity()
    assert connectivity["status"] in ("Ready", "Degraded")
    assert connectivity["provider"] == settings.KNOWLEDGE_STORAGE_PROVIDER

def test_health_endpoint_response():
    response = client.get("/health")
    assert response.status_status_code == 200 if hasattr(response, "status_status_code") else response.status_code == 200
    data = response.json()
    assert "knowledge" in data
    assert "knowledge_storage" in data["knowledge"]
    assert data["knowledge"]["knowledge_storage"]["status"] in ("Ready", "Degraded")
