import pytest
import uuid
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select
import pytest_asyncio

from app.models.base import Base
from app.main import app
from app.core.config import settings
from app.models.identity import User
from app.models.knowledge import (
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeProcessingQueue,
    KnowledgeVersionHistory,
    KnowledgeSource,
    KnowledgeChunk,
    EmbeddingMetadata,
    KnowledgeAnalytics
)
from app.services.knowledge_lifecycle import knowledge_lifecycle_service

# Test database using aiosqlite memory
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession, expire_on_commit=False)

USER_ID = uuid.UUID("e3d5b001-c800-4b82-965a-8b173bf200aa")

@pytest_asyncio.fixture(scope="function")
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestingSessionLocal() as session:
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

@pytest.fixture(autouse=True)
def enable_knowledge_flag(monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)

# ==========================================================
# Service Level Tests
# ==========================================================

@pytest.mark.asyncio
async def test_reindex_document_service(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Test Col", slug="test-col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="test.txt",
        original_file_name="test.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="chk",
        storage_path="path/test.txt",
        size=123,
        processing_status="completed"
    )
    db_session.add(doc)
    await db_session.commit()

    # Pre-populate a queue job
    job = KnowledgeProcessingQueue(document_id=doc.id, status="completed")
    db_session.add(job)
    await db_session.commit()

    # Act
    new_job = await knowledge_lifecycle_service.reindex_document(db_session, doc.id, priority=5, user_id=USER_ID)

    # Assert
    assert new_job.status == "pending"
    assert new_job.priority == 5
    
    # Check old jobs deleted
    jobs_res = await db_session.execute(
        select(KnowledgeProcessingQueue).filter(KnowledgeProcessingQueue.document_id == doc.id)
    )
    jobs = jobs_res.scalars().all()
    assert len(jobs) == 1
    assert jobs[0].id == new_job.id

@pytest.mark.asyncio
async def test_rollback_document_service(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Test Col", slug="test-col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="test.txt",
        original_file_name="test.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="chk",
        storage_path="path/test_v2.txt",
        size=123,
        version=2,
        processing_status="completed"
    )
    db_session.add(doc)

    vh1 = KnowledgeVersionHistory(
        document_id=doc.id,
        version_number=1,
        storage_path="path/test_v1.txt",
        reason="Initial version",
        created_by=USER_ID
    )
    vh2 = KnowledgeVersionHistory(
        document_id=doc.id,
        version_number=2,
        storage_path="path/test_v2.txt",
        reason="Second version",
        created_by=USER_ID
    )
    db_session.add_all([vh1, vh2])
    await db_session.commit()

    # Act
    res = await knowledge_lifecycle_service.rollback_document(
        db_session, document_id=doc.id, target_version=1, user_id=USER_ID, reason="Need rollback"
    )

    # Assert
    assert res["status"] == "success"
    assert res["new_version"] == 3

    # Check updated doc
    await db_session.refresh(doc)
    assert doc.version == 3
    assert doc.storage_path == "path/test_v1.txt"
    assert doc.processing_status == "pending"

    # Check version history added
    history_res = await db_session.execute(
        select(KnowledgeVersionHistory).filter(
            KnowledgeVersionHistory.document_id == doc.id,
            KnowledgeVersionHistory.version_number == 3
        )
    )
    vh3 = history_res.scalars().first()
    assert vh3 is not None
    assert vh3.parent_version_number == 1
    assert vh3.reason == "Need rollback"

@pytest.mark.asyncio
async def test_archive_collection_lifecycle_service(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Test Col", slug="test-col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    doc1 = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="test1.txt",
        original_file_name="test1.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="chk1",
        storage_path="path/test1.txt",
        size=100
    )
    doc2 = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="test2.txt",
        original_file_name="test2.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="chk2",
        storage_path="path/test2.txt",
        size=200
    )
    db_session.add_all([doc1, doc2])
    await db_session.commit()

    # Act
    archived_col = await knowledge_lifecycle_service.archive_collection_lifecycle(db_session, col.id, USER_ID)

    # Assert
    assert archived_col.status == "archived"
    assert archived_col.deleted_at is not None

    # Verify docs archived too
    await db_session.refresh(doc1)
    await db_session.refresh(doc2)
    assert doc1.deleted_at is not None
    assert doc2.deleted_at is not None

@pytest.mark.asyncio
async def test_refresh_source_service(db_session: AsyncSession):
    src = KnowledgeSource(
        id=uuid.uuid4(),
        source_type="internal",
        authority_score=0.8,
        trust_score=0.7
    )
    db_session.add(src)
    await db_session.commit()

    # Act
    refreshed = await knowledge_lifecycle_service.refresh_source(db_session, src.id, USER_ID)

    # Assert
    assert refreshed.authority_score > 0.8
    assert refreshed.trust_score > 0.7

@pytest.mark.asyncio
async def test_health_monitoring_service(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Test Col", slug="test-col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="test.txt",
        original_file_name="test.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="chk",
        storage_path="path/test.txt",
        size=100,
        processing_status="processing",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=15)
    )
    db_session.add(doc)

    # 1. Stuck processing job (running > 5 minutes)
    job = KnowledgeProcessingQueue(
        document_id=doc.id,
        status="running",
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=10)
    )
    db_session.add(job)
    await db_session.commit()

    # 2. Chunk missing embedding
    chunk = KnowledgeChunk(
        id=uuid.uuid4(),
        document_id=doc.id,
        chunk_number=1,
        token_count=10
    )
    db_session.add(chunk)
    await db_session.commit()

    # Act
    health = await knowledge_lifecycle_service.monitor_health(db_session)

    # Assert
    assert health["stuck_jobs_count"] == 1
    assert health["missing_embeddings_count"] == 1
    assert health["unprocessed_documents_count"] == 1
    assert health["status"] == "warning"

@pytest.mark.asyncio
async def test_storage_optimization_service(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Test Col", slug="test-col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="test.txt",
        original_file_name="test.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="chk",
        storage_path="path/test.txt",
        size=100,
        deleted_at=datetime.now(timezone.utc) # Soft deleted document
    )
    db_session.add(doc)
    await db_session.commit()

    chunk = KnowledgeChunk(
        id=uuid.uuid4(),
        document_id=doc.id,
        chunk_number=1,
        token_count=10
    )
    db_session.add(chunk)
    await db_session.commit()

    embed = EmbeddingMetadata(
        chunk_id=chunk.id,
        embedding_model="text-embedding-ada-002",
        embedding_version="v1",
        dimension=1536,
        status="completed"
    )
    db_session.add(embed)
    await db_session.commit()

    # Act
    opt = await knowledge_lifecycle_service.optimize_storage(db_session)

    # Assert
    assert opt["cleaned_chunks_count"] == 1
    assert opt["cleaned_embeddings_count"] == 1

@pytest.mark.asyncio
async def test_analytics_service(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Test Col", slug="test-col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="test.txt",
        original_file_name="test.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="chk",
        storage_path="path/test.txt",
        size=100,
        processing_status="completed"
    )
    db_session.add(doc)
    await db_session.commit()

    # Act
    analytics = await knowledge_lifecycle_service.run_lifecycle_analytics(db_session)

    # Assert
    assert analytics.document_count == 1
    assert analytics.chunk_count == 0  # No chunks added
    assert analytics.processing_count == 0

# ==========================================================
# HTTP / Endpoint Level Tests
# ==========================================================

@pytest.mark.asyncio
async def test_reindex_document_endpoint(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Test Col", slug="test-col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="test.txt",
        original_file_name="test.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="chk",
        storage_path="path/test.txt",
        size=123,
        processing_status="completed"
    )
    db_session.add(doc)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            f"{settings.API_V1_STR}/knowledge/lifecycle/documents/{doc.id}/reindex",
            json={"priority": 3},
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "pending"
    assert data["priority"] == 3

@pytest.mark.asyncio
async def test_rollback_document_endpoint(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Test Col", slug="test-col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="test.txt",
        original_file_name="test.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="chk",
        storage_path="path/test_v2.txt",
        size=123,
        version=2,
        processing_status="completed"
    )
    db_session.add(doc)

    vh1 = KnowledgeVersionHistory(
        document_id=doc.id,
        version_number=1,
        storage_path="path/test_v1.txt",
        reason="Initial version",
        created_by=USER_ID
    )
    vh2 = KnowledgeVersionHistory(
        document_id=doc.id,
        version_number=2,
        storage_path="path/test_v2.txt",
        reason="Second version",
        created_by=USER_ID
    )
    db_session.add_all([vh1, vh2])
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            f"{settings.API_V1_STR}/knowledge/lifecycle/documents/{doc.id}/rollback",
            json={"target_version": 1, "reason": "Accidental edit"},
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "success"
    assert data["new_version"] == 3

@pytest.mark.asyncio
async def test_archive_collection_endpoint(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Test Col", slug="test-col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            f"{settings.API_V1_STR}/knowledge/lifecycle/collections/{col.id}/archive",
            json={"archive_documents": True},
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "archived"

@pytest.mark.asyncio
async def test_health_check_endpoint(db_session: AsyncSession):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            f"{settings.API_V1_STR}/knowledge/lifecycle/health",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "healthy"

@pytest.mark.asyncio
async def test_optimize_endpoint(db_session: AsyncSession):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            f"{settings.API_V1_STR}/knowledge/lifecycle/optimize",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "success"

@pytest.mark.asyncio
async def test_analytics_run_endpoint(db_session: AsyncSession):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            f"{settings.API_V1_STR}/knowledge/lifecycle/analytics/run",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "success"
