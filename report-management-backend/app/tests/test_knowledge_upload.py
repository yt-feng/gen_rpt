import pytest
import io
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import pytest_asyncio

from app.models.base import Base
from app.main import app
from app.core.config import settings
from app.models.knowledge import (
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeProcessingQueue,
    KnowledgeVersionHistory,
    KnowledgeActivityHistory
)
from app.services.knowledge_storage import knowledge_storage_service
from app.services.knowledge_document import knowledge_document_service

client = TestClient(app)

# Use SQLite memory for repository tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession, expire_on_commit=False)

USER_ID = uuid.UUID("e3d5b001-c800-4b82-965a-8b173bf200aa")

@pytest_asyncio.fixture(scope="function")
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestingSessionLocal() as session:
        from app.models.identity import User
        # Seed the mock user to satisfy foreign key relationships
        mock_user = User(
            id=USER_ID,
            full_name="Placeholder Admin",
            email="placeholder@admin.com",
            status="active"
        )
        session.add(mock_user)
        await session.commit()

        # Override FastAPI dependency get_db with TestingSessionLocal
        from app.database.session import get_db
        async def override_get_db():
            yield session
        app.dependency_overrides[get_db] = override_get_db

        # Override get_current_user_placeholder to return our custom mock user
        from app.api.deps import get_current_user_placeholder
        def override_get_current_user_placeholder():
            return {
                "id": str(USER_ID),
                "email": "placeholder@admin.com",
                "full_name": "Placeholder Admin",
                "role": "admin"
            }
        app.dependency_overrides[get_current_user_placeholder] = override_get_current_user_placeholder
        
        yield session
        
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()

# Helper headers matching mock auth credentials
HEADERS = {"Authorization": "Bearer placeholder@admin.com"}

@pytest.fixture(autouse=True)
def enable_knowledge_flag():
    # Force KNOWLEDGE_ENABLED to True for these tests
    with patch.object(settings, "KNOWLEDGE_ENABLED", True):
        yield

@pytest.mark.asyncio
async def test_collection_crud_endpoints(db_session: AsyncSession):
    # 1. Create Collection
    payload = {
        "name": "Test Collection Ingestion",
        "slug": "test-collection-ingestion",
        "description": "Ingestion test workspace",
        "owner_id": str(USER_ID),
        "visibility": "private"
    }
    response = client.post("/api/v1/knowledge/collections", json=payload, headers=HEADERS)
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["name"] == payload["name"]
    coll_id = data["id"]

    # 2. Get Collection
    get_res = client.get(f"/api/v1/knowledge/collections/{coll_id}", headers=HEADERS)
    assert get_res.status_code == 200
    assert get_res.json()["data"]["slug"] == payload["slug"]

    # 3. Update Collection
    patch_res = client.patch(
        f"/api/v1/knowledge/collections/{coll_id}", 
        json={"description": "Updated Ingestion workspace"}, 
        headers=HEADERS
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["data"]["description"] == "Updated Ingestion workspace"

    # 4. List Collections
    list_res = client.get("/api/v1/knowledge/collections", headers=HEADERS)
    assert list_res.status_code == 200
    assert len(list_res.json()["data"]) >= 1

    # 5. Archive Collection
    arch_res = client.post(f"/api/v1/knowledge/collections/{coll_id}/archive", headers=HEADERS)
    assert arch_res.status_code == 200
    assert arch_res.json()["data"]["status"] == "archived"

    # 6. Restore Collection
    rest_res = client.post(f"/api/v1/knowledge/collections/{coll_id}/restore", headers=HEADERS)
    assert rest_res.status_code == 200
    assert rest_res.json()["data"]["status"] == "active"

    # 7. Get Collection Stats
    stats_res = client.get(f"/api/v1/knowledge/collections/{coll_id}/stats", headers=HEADERS)
    assert stats_res.status_code == 200
    stats = stats_res.json()["data"]
    assert "document_count" in stats

    # 8. Delete Collection
    del_res = client.delete(f"/api/v1/knowledge/collections/{coll_id}", headers=HEADERS)
    assert del_res.status_code == 200

    # Ensure deleted collection is not listed
    check_res = client.get(f"/api/v1/knowledge/collections/{coll_id}", headers=HEADERS)
    assert check_res.status_code == 404

@pytest.mark.asyncio
async def test_document_upload_and_validation(db_session: AsyncSession):
    # Setup collection
    coll = KnowledgeCollection(
        name="Doc Ingestion Workspace",
        slug="doc-ingestion-workspace",
        owner_id=USER_ID
    )
    db_session.add(coll)
    await db_session.commit()
    await db_session.refresh(coll)

    # Mock R2 upload provider call to return True
    with patch.object(knowledge_storage_service.provider, "upload", AsyncMock(return_value=True)), \
         patch.object(knowledge_storage_service.provider, "exists", AsyncMock(return_value=True)):
        
        # 1. Successful upload
        file_content = b"PDF dummy contents"
        file_stream = io.BytesIO(file_content)
        
        response = client.post(
            f"/api/v1/knowledge/documents/upload?collection_id={coll.id}",
            files={"file": ("report.pdf", file_stream, "application/pdf")},
            headers=HEADERS
        )
        assert response.status_code == 201
        res_data = response.json()["data"]
        assert res_data["status"] == "success"
        doc_id = res_data["document_id"]

        # Verify DB metadata
        db_doc = await db_session.get(KnowledgeDocument, uuid.UUID(doc_id))
        assert db_doc is not None
        assert db_doc.file_name == "report.pdf"
        assert db_doc.version == 1
        assert db_doc.upload_status == "uploaded"

        # Verify Processing Queue job
        q_res = await db_session.execute(
            select(KnowledgeProcessingQueue).filter(KnowledgeProcessingQueue.document_id == uuid.UUID(doc_id))
        )
        job = q_res.scalars().first()
        assert job is not None
        assert job.status == "pending"

        # 2. Validation Failure: Empty file
        empty_res = client.post(
            f"/api/v1/knowledge/documents/upload?collection_id={coll.id}",
            files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
            headers=HEADERS
        )
        assert empty_res.status_code == 400
        assert "empty" in empty_res.json()["detail"].lower()

        # 3. Validation Failure: Unsupported MIME type / extension
        bad_res = client.post(
            f"/api/v1/knowledge/documents/upload?collection_id={coll.id}",
            files={"file": ("script.py", io.BytesIO(b"print('hello')"), "text/x-python")},
            headers=HEADERS
        )
        assert bad_res.status_code == 400
        assert "unsupported" in bad_res.json()["detail"].lower()

@pytest.mark.asyncio
async def test_duplicate_detection_strategies(db_session: AsyncSession):
    # Setup collection
    coll = KnowledgeCollection(
        name="Duplicate Workspace",
        slug="duplicate-workspace",
        owner_id=USER_ID
    )
    db_session.add(coll)
    await db_session.commit()
    await db_session.refresh(coll)

    with patch.object(knowledge_storage_service.provider, "upload", AsyncMock(return_value=True)), \
         patch.object(knowledge_storage_service.provider, "exists", AsyncMock(return_value=True)):
        
        # Ingest file 1
        file_content = b"Constant check content"
        response1 = client.post(
            f"/api/v1/knowledge/documents/upload?collection_id={coll.id}&duplicate_strategy=skip",
            files={"file": ("doc.md", io.BytesIO(file_content), "text/markdown")},
            headers=HEADERS
        )
        assert response1.status_code == 201
        doc_id = response1.json()["data"]["document_id"]

        # Duplicate Strategy: skip
        response2 = client.post(
            f"/api/v1/knowledge/documents/upload?collection_id={coll.id}&duplicate_strategy=skip",
            files={"file": ("doc_dup.md", io.BytesIO(file_content), "text/markdown")},
            headers=HEADERS
        )
        assert response2.status_code == 201
        assert response2.json()["data"]["status"] == "skipped"

        # Duplicate Strategy: new_version
        response3 = client.post(
            f"/api/v1/knowledge/documents/upload?collection_id={coll.id}&duplicate_strategy=new_version",
            files={"file": ("doc_new_ver.md", io.BytesIO(file_content), "text/markdown")},
            headers=HEADERS
        )
        assert response3.status_code == 201
        assert response3.json()["data"]["status"] == "success"
        assert response3.json()["data"]["version"] == 2

        # Verify version history has multiple versions
        ver_history_res = await db_session.execute(
            select(KnowledgeVersionHistory).filter(KnowledgeVersionHistory.document_id == uuid.UUID(doc_id))
        )
        versions = list(ver_history_res.scalars().all())
        assert len(versions) == 2

@pytest.mark.asyncio
async def test_bulk_upload_endpoint(db_session: AsyncSession):
    coll = KnowledgeCollection(
        name="Bulk Workspace",
        slug="bulk-workspace",
        owner_id=USER_ID
    )
    db_session.add(coll)
    await db_session.commit()
    await db_session.refresh(coll)

    with patch.object(knowledge_storage_service.provider, "upload", AsyncMock(return_value=True)), \
         patch.object(knowledge_storage_service.provider, "exists", AsyncMock(return_value=True)):
        
        # Ingest bulk docs (one valid, one empty)
        response = client.post(
            f"/api/v1/knowledge/documents/bulk-upload?collection_id={coll.id}",
            files=[
                ("files", ("file1.md", io.BytesIO(b"Valid MD content"), "text/markdown")),
                ("files", ("file2.pdf", io.BytesIO(b""), "application/pdf"))
            ],
            headers=HEADERS
        )
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["total_files"] == 2
        assert data["success_count"] == 1
        assert data["failure_count"] == 1

@pytest.mark.asyncio
async def test_document_move_archive_restore(db_session: AsyncSession):
    coll1 = KnowledgeCollection(
        name="Collection 1",
        slug="collection-1",
        owner_id=USER_ID
    )
    coll2 = KnowledgeCollection(
        name="Collection 2",
        slug="collection-2",
        owner_id=USER_ID
    )
    db_session.add_all([coll1, coll2])
    await db_session.commit()
    await db_session.refresh(coll1)
    await db_session.refresh(coll2)

    # Setup doc
    doc = KnowledgeDocument(
        collection_id=coll1.id,
        file_name="doc.txt",
        original_file_name="doc.txt",
        mime_type="text/plain",
        extension=".txt",
        checksum="checksum",
        storage_path="knowledge/doc.txt",
        size=10,
        upload_status="uploaded"
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    # 1. Move Document
    move_res = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/move?target_collection_id={coll2.id}",
        headers=HEADERS
    )
    assert move_res.status_code == 200
    db_doc = await db_session.get(KnowledgeDocument, doc.id)
    assert db_doc.collection_id == coll2.id

    # 2. Archive Document
    arch_res = client.delete(
        f"/api/v1/knowledge/documents/{doc.id}?reason=testing",
        headers=HEADERS
    )
    assert arch_res.status_code == 200
    db_doc_arch = await db_session.get(KnowledgeDocument, doc.id)
    assert db_doc_arch.deleted_at is not None

    # 3. Restore Document
    rest_res = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/restore",
        headers=HEADERS
    )
    assert rest_res.status_code == 200
    db_doc_rest = await db_session.get(KnowledgeDocument, doc.id)
    assert db_doc_rest.deleted_at is None

@pytest.mark.asyncio
async def test_processing_queue_endpoints(db_session: AsyncSession):
    # Setup doc & queue job
    coll = KnowledgeCollection(
        name="Queue Workspace",
        slug="queue-workspace",
        owner_id=USER_ID
    )
    db_session.add(coll)
    await db_session.commit()
    await db_session.refresh(coll)

    doc = KnowledgeDocument(
        collection_id=coll.id,
        file_name="doc_queue.txt",
        original_file_name="doc_queue.txt",
        mime_type="text/plain",
        extension=".txt",
        checksum="checksum_queue",
        storage_path="knowledge/doc_queue.txt",
        size=10,
        upload_status="uploaded"
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    job = KnowledgeProcessingQueue(
        document_id=doc.id,
        status="pending"
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # 1. List Jobs
    list_res = client.get("/api/v1/knowledge/queue/status", headers=HEADERS)
    assert list_res.status_code == 200
    assert len(list_res.json()["data"]) >= 1

    # 2. Detail of specific job
    detail_res = client.get(f"/api/v1/knowledge/queue/{job.id}", headers=HEADERS)
    assert detail_res.status_code == 200
    assert detail_res.json()["data"]["document_id"] == str(doc.id)

@pytest.mark.asyncio
async def test_upload_rollback_on_db_fail(db_session: AsyncSession):
    """
    Task 15: Rollback uploaded R2 object if DB registration fails.
    """
    coll = KnowledgeCollection(
        name="Rollback Workspace",
        slug="rollback-workspace",
        owner_id=USER_ID
    )
    db_session.add(coll)
    await db_session.commit()
    await db_session.refresh(coll)

    mock_upload = AsyncMock(return_value=True)
    mock_exists = AsyncMock(return_value=True)
    mock_delete = AsyncMock(return_value=True)

    with patch.object(knowledge_storage_service.provider, "upload", mock_upload), \
         patch.object(knowledge_storage_service.provider, "exists", mock_exists), \
         patch.object(knowledge_storage_service.provider, "delete", mock_delete):
        
        # Simulate database committing error by mocking session commit
        with patch.object(db_session, "commit", side_effect=Exception("Database simulation crash")):
            with pytest.raises(Exception):
                await knowledge_document_service.upload_document(
                    db=db_session,
                    collection_id=coll.id,
                    filename="report.pdf",
                    file_data=b"dummy contents",
                    content_type="application/pdf",
                    user_id=USER_ID
                )
            
            # Ensure delete was called on storage provider to clean up R2
            assert mock_delete.called is True
