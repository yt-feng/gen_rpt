import pytest
import uuid
import hashlib
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select
import pytest_asyncio

from app.models.base import Base
from app.main import app
from app.core.config import settings
from app.models.identity import User
from app.models.document import Document, DocumentVersion, DocumentSection, DocumentBlock
from app.models.enums import DocStatus, DocChangeType, ReleaseStatus, BlockContentType
from app.models.knowledge import (
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeChunk,
    EmbeddingMetadata,
    RetrievalSession,
    KnowledgeActivityHistory
)
from app.models.rag_integration import EvidenceAttribution, GenerationAnalytics

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
                "role": "admin",
                "organization_id": "00000000-0000-0000-0000-000000000000"
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
# Tests
# ==========================================================

@pytest.mark.asyncio
async def test_intelligence_analytics_endpoint(db_session: AsyncSession):
    # Seed collection, doc, chunk
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=USER_ID)
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
        size=100
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            f"{settings.API_V1_STR}/knowledge/intelligence/analytics",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["growth_metrics"]["collections_count"] == 1
    assert data["growth_metrics"]["documents_count"] == 1
    assert data["growth_metrics"]["chunks_count"] == 1

@pytest.mark.asyncio
async def test_intelligence_recommendations_endpoint(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            f"{settings.API_V1_STR}/knowledge/intelligence/recommendations",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["related_collections"]) == 1
    assert data["related_collections"][0]["name"] == "Col"

@pytest.mark.asyncio
async def test_knowledge_reuse_endpoint(db_session: AsyncSession):
    attr = EvidenceAttribution(
        generation_job_id=uuid.uuid4(),
        supporting_chunks={},
        confidence=0.9
    )
    db_session.add(attr)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            f"{settings.API_V1_STR}/knowledge/intelligence/reuse",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["shared_evidence_count"] == 1

@pytest.mark.asyncio
async def test_ingest_approved_report(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    # Create approved report
    report = Document(
        id=uuid.uuid4(),
        title="Approved Report",
        slug="approved-report",
        status=DocStatus.approved,
        owner_id=USER_ID
    )
    db_session.add(report)
    await db_session.commit()

    version = DocumentVersion(
        id=uuid.uuid4(),
        document_id=report.id,
        version_number=1,
        change_type=DocChangeType.AI_GENERATION,
        status=DocStatus.approved,
        release_status=ReleaseStatus.Approved,
        snapshot_markdown_url="reports/snap.md"
    )
    db_session.add(version)
    await db_session.commit()

    report.current_version_id = version.id
    db_session.add(report)
    await db_session.commit()

    section = DocumentSection(
        id=uuid.uuid4(),
        version_id=version.id,
        stable_id="s1",
        section_order=1,
        title="Intro"
    )
    db_session.add(section)
    await db_session.commit()

    block = DocumentBlock(
        id=uuid.uuid4(),
        section_id=section.id,
        stable_id="b1",
        block_order=1,
        block_type=BlockContentType.paragraph,
        markdown="This is approved content to ingest."
    )
    db_session.add(block)
    await db_session.commit()

    # Act: post request
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            f"{settings.API_V1_STR}/knowledge/intelligence/ingest-report",
            json={"report_id": str(report.id), "target_collection_id": str(col.id)},
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["status"] == "success"
    assert data["chunks_count"] == 1

@pytest.mark.asyncio
async def test_ingest_draft_report_fails(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    # Create draft report
    report = Document(
        id=uuid.uuid4(),
        title="Draft Report",
        slug="draft-report",
        status=DocStatus.draft,
        owner_id=USER_ID
    )
    db_session.add(report)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            f"{settings.API_V1_STR}/knowledge/intelligence/ingest-report",
            json={"report_id": str(report.id), "target_collection_id": str(col.id)},
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 400
    assert "Only approved" in response.json()["detail"]

@pytest.mark.asyncio
async def test_sharing_endpoint(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Shared Col", slug="shared-col", owner_id=USER_ID, visibility="shared")
    db_session.add(col)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            f"{settings.API_V1_STR}/knowledge/intelligence/sharing",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["shared_collections"]) == 1
    assert data["shared_collections"][0]["name"] == "Shared Col"

@pytest.mark.asyncio
async def test_quality_endpoint(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=USER_ID)
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
        validation_status="validated"
    )
    db_session.add(doc)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            f"{settings.API_V1_STR}/knowledge/intelligence/quality",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["validation_score"] == 1.0
    assert data["overall_quality_score"] == 0.95

@pytest.mark.asyncio
async def test_retrieval_performance_endpoint(db_session: AsyncSession):
    ga = GenerationAnalytics(
        generation_job_id=uuid.uuid4(),
        retrieval_time_ms=150,
        cache_hit=True
    )
    db_session.add(ga)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            f"{settings.API_V1_STR}/knowledge/intelligence/retrieval-performance",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["average_latency_ms"] == 150.0
    assert data["cache_hit_rate"] == 1.0

@pytest.mark.asyncio
async def test_embeddings_and_migration_endpoints(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=USER_ID)
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
        size=100
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

    # 1. Test status endpoint
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            f"{settings.API_V1_STR}/knowledge/intelligence/embeddings",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    assert response.status_code == 200
    assert response.json()["data"]["total_embeddings_count"] == 0

    # 2. Test migration endpoint
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            f"{settings.API_V1_STR}/knowledge/intelligence/embeddings/migrate",
            json={
                "source_model": "text-embedding-ada-002",
                "target_model": "text-embedding-3-small",
                "collection_id": str(col.id)
            },
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    assert response.status_code == 200
    assert response.json()["data"]["migrated_count"] == 1

@pytest.mark.asyncio
async def test_governance_endpoint(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="old_test.txt",
        original_file_name="old_test.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="chk",
        storage_path="path/old_test.txt",
        size=100,
        created_at=datetime.now(timezone.utc) - timedelta(days=400) # Exceeds 365 days
    )
    db_session.add(doc)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            f"{settings.API_V1_STR}/knowledge/intelligence/governance",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["retention_flagged_count"] == 1
    assert data["policy_compliance_rate"] == 0.0

@pytest.mark.asyncio
async def test_audit_endpoint(db_session: AsyncSession):
    log = KnowledgeActivityHistory(
        id=uuid.uuid4(),
        activity_type="validation",
        details={"status": "success"}
    )
    db_session.add(log)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            f"{settings.API_V1_STR}/knowledge/intelligence/audit",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["logs"]) == 1

@pytest.mark.asyncio
async def test_connectors_endpoint(db_session: AsyncSession):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            f"{settings.API_V1_STR}/knowledge/intelligence/connectors",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["connectors"]) > 0

@pytest.mark.asyncio
async def test_improvements_endpoint(db_session: AsyncSession):
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=USER_ID)
    db_session.add(col)
    await db_session.commit()

    # Create duplicates
    doc1 = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="dup1.txt",
        original_file_name="dup1.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="identical",
        storage_path="path/dup1.txt",
        size=100
    )
    doc2 = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="dup2.txt",
        original_file_name="dup2.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="identical",
        storage_path="path/dup2.txt",
        size=100
    )
    db_session.add_all([doc1, doc2])
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            f"{settings.API_V1_STR}/knowledge/intelligence/improvements",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["duplicate_documents"]) == 2
    assert len(data["suggestions"]) > 0

@pytest.mark.asyncio
async def test_health_check_includes_sub_engines(monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            f"{settings.API_V1_STR}/knowledge/health",
            headers={"Authorization": "Bearer placeholder@admin.com"}
        )
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["knowledge_intelligence_engine"] == "healthy"
    assert data["analytics_engine"] == "healthy"
    assert data["recommendation_engine"] == "healthy"
    assert data["knowledge_quality_engine"] == "healthy"
    assert data["governance_engine"] == "healthy"
    assert data["audit_engine"] == "healthy"
    assert data["connector_framework"] == "healthy"
    assert data["continuous_improvement_engine"] == "healthy"
