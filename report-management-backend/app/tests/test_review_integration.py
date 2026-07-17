import pytest
import uuid
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from httpx import AsyncClient, ASGITransport

from app.models.base import Base
from app.models.document import Document, DocumentVersion, DocumentSection, DocumentBlock
from app.models.workflow import GenerationJob
from app.models.review import HumanReview
from app.models.knowledge import (
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeChunk
)
from app.models.validation import (
    ValidationPolicy,
    ValidationReport
)
from app.models.rag_integration import (
    KnowledgeSnapshot,
    EvidenceAttribution
)
from app.models.review_integration import (
    ReviewSnapshot,
    ReviewAnalytics
)
from app.services.review_integration import (
    evidence_verification_service,
    citation_verification_service,
    traceability_service,
    review_snapshot_service,
    evidence_viewer_service,
    validation_dashboard_service,
    review_analytics_service
)
from app.services.validation import policy_service
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


@pytest.mark.anyio
async def test_review_integration_services(db_session: AsyncSession):
    # Enable feature flags
    settings.RAG_ENABLED = True
    settings.VALIDATION_ENABLED = True

    # Seed User
    user_id = uuid.UUID("a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d")
    
    # 1. Create Collection, Document, Chunk
    col = KnowledgeCollection(id=uuid.uuid4(), name="Internal Knowledge Base", slug="ikb", owner_id=user_id, status="active")
    db_session.add(col)
    await db_session.commit()
    await policy_service.create_default_policy(db_session)

    kdoc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="fusion_economics.pdf",
        original_file_name="Nuclear Fusion Economics Roadmap.pdf",
        mime_type="application/pdf",
        extension="pdf",
        checksum="dummychecksum123",
        storage_path="/path/to/fusion_economics.pdf",
        size=1024,
        language="en",
        processing_status="completed",
        validation_status="validated",
        created_at=datetime.now(timezone.utc) - timedelta(days=10)
    )
    db_session.add(kdoc)
    await db_session.commit()

    kchunk = KnowledgeChunk(
        id=uuid.uuid4(),
        document_id=kdoc.id,
        chunk_number=1,
        character_count=450,
        token_count=100,
        chunk_metadata={
            "content": "Commercial nuclear fusion pilot plant costs are estimated at 5 billion USD. General economics target a levelized cost of electricity below 50 USD per MWh by 2035.",
            "embedding": [0.1] * 1536
        }
    )
    db_session.add(kchunk)
    await db_session.commit()

    # 2. Create Generation Document, Version, Section, Block
    doc = Document(id=uuid.uuid4(), title="Fusion Energy Outlook", slug="fusion-energy-outlook", owner_id=user_id, status="draft")

    db_session.add(doc)
    await db_session.commit()

    version = DocumentVersion(
        id=uuid.uuid4(),
        document_id=doc.id,
        version_number=1,
        status="generated",
        change_type="AI_GENERATION",
        created_by=user_id
    )

    db_session.add(version)
    await db_session.commit()

    doc.current_version_id = version.id
    await db_session.commit()

    section = DocumentSection(
        id=uuid.uuid4(),
        version_id=version.id,
        stable_id="econ-sec",
        section_order=1,
        title="Economics"
    )
    db_session.add(section)
    await db_session.commit()

    from app.models.enums import BlockContentType
    block = DocumentBlock(
        id=uuid.uuid4(),
        section_id=section.id,
        block_type=BlockContentType.paragraph,
        markdown="According to the Fusion Roadmap [Nuclear Fusion Economics Roadmap.pdf], pilot plant cost is estimated at 5 billion USD.",
        block_order=1,
        stable_id="econ-block-1"
    )
    db_session.add(block)
    await db_session.commit()


    # 3. Create GenerationJob & Snapshots
    job = GenerationJob(
        id=uuid.uuid4(),
        document_id=doc.id,
        topic="fusion",
        status="completed",
        completed=datetime.now(timezone.utc)
    )
    db_session.add(job)
    await db_session.commit()

    snapshot = KnowledgeSnapshot(
        id=uuid.uuid4(),
        knowledge_version="1.0.0",
        collections_used={"collections": [str(col.id)]},
        documents_used={"documents": [{
            "id": str(kdoc.id),
            "file_name": kdoc.file_name,
            "original_file_name": kdoc.original_file_name,
            "freshness_score": 1.0,
            "authority_score": 1.0
        }]},
        chunks_used={"chunks": [{
            "id": str(kchunk.id),
            "content": "Commercial nuclear fusion pilot plant costs are estimated at 5 billion USD."
        }]},
        embedding_version="1.0",
        validation_version="1.0"
    )
    db_session.add(snapshot)
    await db_session.commit()

    report = ValidationReport(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        confidence_scores={"overall_confidence": 0.95},
        validation_summary="healthy"
    )

    db_session.add(report)
    await db_session.commit()

    attr = EvidenceAttribution(
        id=uuid.uuid4(),
        generation_job_id=job.id,
        section_id=str(section.id),
        supporting_chunks={"chunks": [{
            "id": str(kchunk.id),
            "document_id": str(kdoc.id),
            "document_name": kdoc.original_file_name,
            "collection_id": str(col.id),
            "collection_name": col.name,
            "content": "Commercial nuclear fusion pilot plant costs are estimated at 5 billion USD.",
            "similarity_score": 0.85,
            "authority_score": 1.0,
            "freshness_score": 1.0,
            "confidence_score": 0.95
        }]},
        supporting_documents={"documents": [{"id": str(kdoc.id)}]},
        supporting_sources={"sources": []},
        supporting_collections={"collections": [{"id": str(col.id)}]},
        confidence=0.95,
        validation_report_id=report.id,
        snapshot_id=snapshot.id
    )
    db_session.add(attr)
    await db_session.commit()

    # --- Test Service Layer ---
    # Evidence Verification
    verif = await evidence_verification_service.verify_evidence(db_session, version.id)
    assert verif["evidence_quality"] == 0.95
    assert len(verif["unsupported_statements"]) == 0

    # Citation Verification
    citations = await citation_verification_service.verify_citations(db_session, version.id)
    assert len(citations["citations"]) == 1
    assert citations["citations"][0]["status"] == "valid"
    assert citations["citations"][0]["referenced_document_exists"] is True

    # Traceability
    trace = await traceability_service.get_traceability(db_session, version.id)
    assert len(trace["traceability_nodes"]) == 1
    assert trace["traceability_nodes"][0]["node_stable_id"] == "econ-block-1"

    # Review Snapshot
    snap = await review_snapshot_service.create_review_snapshot(
        db=db_session,
        version_id=version.id,
        reviewer_id=user_id
    )
    assert snap.knowledge_snapshot_id == snapshot.id
    assert snap.reviewer_id == user_id

    # Retrieve Review Snapshot
    snap_details = await review_snapshot_service.get_review_snapshot(db_session, snap.id)
    assert snap_details["confidence_score"] == 0.95


@pytest.mark.anyio
async def test_review_integration_api(db_session: AsyncSession):
    # Enable feature flags
    settings.RAG_ENABLED = True
    settings.VALIDATION_ENABLED = True

    user_id = uuid.UUID("a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d")
    
    # Setup baseline data
    col = KnowledgeCollection(id=uuid.uuid4(), name="Internal KB", slug="ikb", owner_id=user_id, status="active")
    db_session.add(col)
    await db_session.commit()

    doc = Document(id=uuid.uuid4(), title="AI Review Test", slug="ai-review-test", owner_id=user_id, status="draft")

    db_session.add(doc)
    await db_session.commit()

    version = DocumentVersion(id=uuid.uuid4(), document_id=doc.id, version_number=1, status="generated", change_type="AI_GENERATION")

    db_session.add(version)
    await db_session.commit()

    doc.current_version_id = version.id
    await db_session.commit()

    # Simulate completed generation job
    job = GenerationJob(
        id=uuid.uuid4(),
        document_id=doc.id,
        topic="ai",
        status="completed",
        completed=datetime.now(timezone.utc)
    )
    db_session.add(job)
    await db_session.commit()

    # Test routes
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Health
        resp = await ac.get("/health")
        assert resp.status_code == 200
        health_data = resp.json()
        assert "review_integration" in health_data
        assert health_data["review_integration"]["status"] == "healthy"

        # 2. Viewer
        resp = await ac.get(f"/api/v1/reviews/viewer/{version.id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["version_id"] == str(version.id)

        # 3. Browser
        resp = await ac.get("/api/v1/reviews/browser")
        assert resp.status_code == 200

        # 4. Traceability
        resp = await ac.get(f"/api/v1/reviews/traceability/{version.id}")
        assert resp.status_code == 200

        # 5. Verification
        resp = await ac.get(f"/api/v1/reviews/verification/{version.id}")
        assert resp.status_code == 200

        # 6. Citations
        resp = await ac.get(f"/api/v1/reviews/citations/{version.id}")
        assert resp.status_code == 200

        # 7. Dashboard
        resp = await ac.get(f"/api/v1/reviews/dashboard/{version.id}")
        assert resp.status_code == 200

        # 8. Analytics
        resp = await ac.get("/api/v1/reviews/analytics")
        assert resp.status_code == 200
