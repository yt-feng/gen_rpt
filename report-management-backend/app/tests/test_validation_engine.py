import pytest
import uuid
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from httpx import AsyncClient, ASGITransport

from app.models.base import Base
from app.models.knowledge import (
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeSource,
    KnowledgeChunk,
    RetrievalSession,
    RetrievalResult
)
from app.models.validation import (
    ValidationPolicy,
    ValidationReport,
    ValidationHistory,
    ValidationAuditLog
)
from app.services.validation import (
    policy_service,
    source_validation_service,
    authority_service,
    freshness_service,
    duplicate_service,
    conflict_service,
    confidence_service,
    evidence_service,
    validation_service
)
from app.main import app
from app.core.config import settings

# Override DB settings for testing in SQLite memory
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import pytest_asyncio

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession, expire_on_commit=False)

@pytest_asyncio.fixture(scope="function", autouse=True)
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestingSessionLocal() as session:
        from app.database.session import get_db
        from app.api.deps import get_current_user_placeholder
        
        async def override_get_db():
            yield session
            
        async def override_get_current_user():
            return {
                "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
                "email": "test-admin@gatex.com",
                "full_name": "Test Admin",
                "role": "admin"
            }
            
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user_placeholder] = override_get_current_user
        yield session
        
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_validation_policy_crud(db_session):
    # Test creating default policy
    policy = await policy_service.get_active_policy(db_session)
    assert policy is not None
    assert policy.is_active is True
    assert policy.min_authority == 0.5
    
    # Test create new policy (deactivates older ones)
    from app.schemas.validation import ValidationPolicyCreate
    new_policy_schema = ValidationPolicyCreate(
        name="Strict Policy",
        is_active=True,
        min_authority=0.8,
        min_freshness=0.7,
        min_confidence=0.7,
        max_duplicate_ratio=0.1,
        min_sources=3,
        conflict_threshold=0.3,
        knowledge_quality_threshold=0.8
    )
    new_policy = await policy_service.create_policy(db_session, new_policy_schema)
    assert new_policy.min_authority == 0.8
    assert new_policy.is_active is True
    
    # Verify previous policy is deactivated
    old_policy = await policy_service.get_policy(db_session, policy.id)
    assert old_policy.is_active is False

@pytest.mark.asyncio
async def test_source_validation_service(db_session):
    policy = await policy_service.get_active_policy(db_session)
    user_id = uuid.uuid4()
    
    # Create valid collection and document
    col = KnowledgeCollection(id=uuid.uuid4(), name="Active Col", slug="active-col", owner_id=user_id, status="active")
    db_session.add(col)
    await db_session.commit()
    
    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="valid.pdf",
        original_file_name="valid.pdf",
        mime_type="application/pdf",
        extension="pdf",
        checksum="checksum123",
        storage_path="r2/valid.pdf",
        size=1000,
        processing_status="completed",
        validation_status="validated"
    )
    db_session.add(doc)
    await db_session.commit()
    
    # Document sources
    source = KnowledgeSource(
        id=uuid.uuid4(),
        document_id=doc.id,
        source_type="government",
        publisher="Gov Corp"
    )
    db_session.add(source)
    await db_session.commit()

    # Validate
    results, errors = await source_validation_service.validate_sources(db_session, [doc.id], policy)
    assert doc.id in results
    assert results[doc.id]["is_valid"] is True
    assert len(errors) == 0

@pytest.mark.asyncio
async def test_authority_scoring(db_session):
    policy = await policy_service.get_active_policy(db_session)
    user_id = uuid.uuid4()
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=user_id)
    db_session.add(col)
    await db_session.commit()
    
    doc_gov = KnowledgeDocument(id=uuid.uuid4(), collection_id=col.id, file_name="gov.pdf", original_file_name="gov.pdf", mime_type="application/pdf", extension="pdf", checksum="c1", storage_path="p1", size=100)
    doc_unk = KnowledgeDocument(id=uuid.uuid4(), collection_id=col.id, file_name="unk.pdf", original_file_name="unk.pdf", mime_type="application/pdf", extension="pdf", checksum="c2", storage_path="p2", size=100)
    db_session.add_all([doc_gov, doc_unk])
    await db_session.commit()
    
    src_gov = KnowledgeSource(id=uuid.uuid4(), document_id=doc_gov.id, source_type="government")
    src_unk = KnowledgeSource(id=uuid.uuid4(), document_id=doc_unk.id, source_type="unknown")
    db_session.add_all([src_gov, src_unk])
    await db_session.commit()
    
    scores = await authority_service.calculate_authority(db_session, [doc_gov, doc_unk], policy)
    assert scores[doc_gov.id] == 1.0
    assert scores[doc_unk.id] == 0.3

@pytest.mark.asyncio
async def test_freshness_decay(db_session):
    policy = await policy_service.get_active_policy(db_session)
    user_id = uuid.uuid4()
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=user_id)
    db_session.add(col)
    await db_session.commit()
    
    # Old document
    doc_old = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="old.pdf",
        original_file_name="old.pdf",
        mime_type="a", extension="pdf", checksum="c1", storage_path="p1", size=100,
        created_at=datetime.now(timezone.utc) - timedelta(days=730)
    )
    # Fresh document
    doc_new = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="new.pdf",
        original_file_name="new.pdf",
        mime_type="a", extension="pdf", checksum="c2", storage_path="p2", size=100,
        created_at=datetime.now(timezone.utc) - timedelta(days=10)
    )
    db_session.add_all([doc_old, doc_new])
    await db_session.commit()
    
    scores = await freshness_service.calculate_freshness(db_session, [doc_old, doc_new], policy)
    assert scores[doc_old.id] < scores[doc_new.id]
    assert scores[doc_new.id] > 0.9

@pytest.mark.asyncio
async def test_duplicate_validation(db_session):
    policy = await policy_service.get_active_policy(db_session)
    doc_id1 = uuid.uuid4()
    doc_id2 = uuid.uuid4()
    
    chunks = [
        {"chunk_id": uuid.uuid4(), "document_id": doc_id1, "text_content": "This is identical context data.", "similarity_score": 0.9},
        {"chunk_id": uuid.uuid4(), "document_id": doc_id2, "text_content": "This is identical context data.", "similarity_score": 0.8}
    ]
    
    auth_scores = {doc_id1: 0.9, doc_id2: 0.7}
    dup_flags, dup_analysis = await duplicate_service.analyze_duplicates(db_session, chunks, auth_scores, policy)
    
    # Higher quality chunk should be False (not duplicate), lower quality chunk should be True
    assert dup_flags[chunks[0]["chunk_id"]] is False
    assert dup_flags[chunks[1]["chunk_id"]] is True
    assert dup_analysis["duplicate_chunks_count"] == 1

@pytest.mark.asyncio
async def test_conflict_detection(db_session):
    policy = await policy_service.get_active_policy(db_session)
    doc_id1 = uuid.uuid4()
    doc_id2 = uuid.uuid4()
    
    chunks = [
        {"chunk_id": uuid.uuid4(), "document_id": doc_id1, "file_name": "a.txt", "text_content": "The inflation rate in Saudi Arabia in 2024 was 2.5 percent.", "metadata": {"heading": "Inflation"}},
        {"chunk_id": uuid.uuid4(), "document_id": doc_id2, "file_name": "b.txt", "text_content": "Saudi Arabia reported a 4.1 percent inflation rate in 2024.", "metadata": {"heading": "Inflation"}}
    ]
    
    conflict_map, conflicts_list = await conflict_service.detect_conflicts(db_session, chunks, policy)
    assert len(conflicts_list) > 0
    assert chunks[0]["chunk_id"] in conflict_map
    assert chunks[1]["chunk_id"] in conflict_map

@pytest.mark.asyncio
async def test_validation_engine_session_orchestration(db_session, monkeypatch):
    monkeypatch.setattr(settings, "VALIDATION_ENABLED", True)
    
    user_id = uuid.uuid4()
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=user_id, status="active")
    db_session.add(col)
    await db_session.commit()
    
    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="engine_test.pdf",
        original_file_name="engine_test.pdf",
        mime_type="application/pdf",
        extension="pdf",
        checksum="checksum_engine",
        storage_path="r2/p.pdf",
        size=1000,
        processing_status="completed",
        validation_status="validated"
    )
    db_session.add(doc)
    await db_session.commit()
    
    source = KnowledgeSource(id=uuid.uuid4(), document_id=doc.id, source_type="internal", publisher="Internal Publisher")
    db_session.add(source)
    await db_session.commit()
    
    chunk = KnowledgeChunk(
        id=uuid.uuid4(),
        document_id=doc.id,
        chunk_number=1,
        token_count=100,
        chunk_metadata={"content": "Core validated enterprise context."}
    )
    db_session.add(chunk)
    await db_session.commit()
    
    # Mock retrieval session
    session = RetrievalSession(
        id=uuid.uuid4(),
        query="validated enterprise",
        status="completed",
        snapshot_metadata={
            "knowledge_version": "1.0.0",
            "collections": [str(col.id)],
            "documents": [str(doc.id)],
            "chunks": [str(chunk.id)]
        }
    )
    db_session.add(session)
    await db_session.commit()
    
    result = RetrievalResult(
        id=uuid.uuid4(),
        session_id=session.id,
        chunk_id=chunk.id,
        similarity_score=0.85,
        ranking=1,
        confidence=1.0,
        source_id=source.id
    )
    db_session.add(result)
    await db_session.commit()
    
    # Run full engine validation
    package = await validation_service.validate_session(db_session, session.id, user_id)
    assert package is not None
    assert package.context_metadata["overall_confidence"] > 0.5
    assert len(package.validated_chunks) == 1
    assert package.validated_chunks[0].validation_status == "validated"
    
    # Verify ValidationReport, ValidationHistory, and ValidationAuditLog rows created
    rep_stmt = select(ValidationReport).where(ValidationReport.session_id == session.id)
    rep_res = await db_session.execute(rep_stmt)
    report = rep_res.scalar_one_or_none()
    assert report is not None
    
    hist_stmt = select(ValidationHistory).where(ValidationHistory.session_id == session.id)
    hist_res = await db_session.execute(hist_stmt)
    assert hist_res.scalar_one_or_none() is not None
    
    # Debug SQLite database columns and types via raw SQL
    try:
        from sqlalchemy import text
        raw_res = await db_session.execute(text("SELECT * FROM knowledge_collections"))
        print("RAW COLLECTIONS ROWS:", raw_res.all())
        
        chunks_res = await db_session.execute(select(KnowledgeChunk))
        for c in chunks_res.scalars().all():
            print("CHUNK ID:", c.id, type(c.id))
            print("CHUNK DOC ID:", c.document_id, type(c.document_id))
    except Exception as e:
        print("DEBUG FAILED:", e)
    
    audit_stmt = select(ValidationAuditLog)
    audit_res = await db_session.execute(audit_stmt)
    assert len(audit_res.scalars().all()) > 0

@pytest.mark.asyncio
async def test_validation_api_endpoints(db_session, monkeypatch):
    monkeypatch.setattr(settings, "VALIDATION_ENABLED", True)
    
    # Set up DB data similar to previous test
    user_id = uuid.UUID("a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d")
    
    # Seed the mock user to satisfy foreign key relationships
    from app.models.identity import User
    user = User(
        id=user_id,
        full_name="Test Admin",
        email="test-admin@gatex.com",
        status="active"
    )
    db_session.add(user)
    await db_session.commit()
    
    col = KnowledgeCollection(id=uuid.uuid4(), name="Col", slug="col", owner_id=user_id, status="active")
    db_session.add(col)
    await db_session.commit()
    
    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="api_test.pdf",
        original_file_name="api_test.pdf",
        mime_type="application/pdf",
        extension="pdf",
        checksum="c1",
        storage_path="p1",
        size=100,
        processing_status="completed",
        validation_status="validated"
    )
    db_session.add(doc)
    await db_session.commit()
    
    source = KnowledgeSource(
        id=uuid.uuid4(),
        document_id=doc.id,
        source_type="government",
        publisher="Gov Publisher"
    )
    db_session.add(source)
    await db_session.commit()
    
    chunk = KnowledgeChunk(id=uuid.uuid4(), document_id=doc.id, chunk_number=1, chunk_metadata={"content": "Validated API content."})
    db_session.add(chunk)
    await db_session.commit()
    
    session = RetrievalSession(
        id=uuid.uuid4(),
        query="validated API",
        status="completed",
        snapshot_metadata={"knowledge_version": "1.0.0", "collections": [str(col.id)], "documents": [str(doc.id)], "chunks": [str(chunk.id)]}
    )
    db_session.add(session)
    await db_session.commit()
    
    result = RetrievalResult(
        id=uuid.uuid4(),
        session_id=session.id,
        chunk_id=chunk.id,
        similarity_score=0.9,
        ranking=1,
        confidence=1.0,
        source_id=source.id
    )
    db_session.add(result)
    await db_session.commit()
    
    # API testing client
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Mock auth header (overridden in db_session fixture)
        headers = {"Authorization": "Bearer test-token"}






        
        # 1. Trigger Validation
        resp = await client.post(f"/api/v1/validation/validate?session_id={session.id}", headers=headers)
        print("API RESPONSE:", resp.text)
        assert resp.status_code == 200
        data = resp.json()["data"]

        assert data["validation_report_reference"] is not None
        
        # 2. Get Report
        rep_id = data["validation_report_reference"]
        resp_rep = await client.get(f"/api/v1/validation/reports/{rep_id}", headers=headers)
        assert resp_rep.status_code == 200
        assert resp_rep.json()["data"]["id"] == rep_id
        
        # 3. Get History
        resp_hist = await client.get("/api/v1/validation/history", headers=headers)
        assert resp_hist.status_code == 200
        assert len(resp_hist.json()["data"]) > 0
        
        # 4. Get Statistics
        resp_stats = await client.get("/api/v1/validation/statistics", headers=headers)
        assert resp_stats.status_code == 200
        assert resp_stats.json()["data"]["validation_requests_count"] == 1
        
        # 5. Get Summary
        resp_sum = await client.get("/api/v1/validation/summary", headers=headers)
        assert resp_sum.status_code == 200
        assert len(resp_sum.json()["data"]) == 1
        
        # 6. Policies List
        resp_pols = await client.get("/api/v1/validation/policies", headers=headers)
        assert resp_pols.status_code == 200
        assert len(resp_pols.json()["data"]) > 0
        
        # 7. Health Endpoint
        resp_health = await client.get("/api/v1/validation/health", headers=headers)
        assert resp_health.status_code == 200
        assert resp_health.json()["data"]["validation_engine"] == "healthy"
