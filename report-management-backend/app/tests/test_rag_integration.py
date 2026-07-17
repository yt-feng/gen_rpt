import pytest
import uuid
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
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
    ValidationReport
)
from app.models.rag_integration import (
    KnowledgeSnapshot,
    EvidenceAttribution,
    GenerationAnalytics,
    GenerationContextCache
)
from app.services.rag_integration import (
    generation_context_service,
    knowledge_snapshot_service,
    evidence_attribution_service,
    generation_analytics_service,
    context_cache_service,
    ai_gateway_service
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


@pytest.mark.anyio
async def test_rag_context_preparation_and_cache(db_session: AsyncSession):
    # Enable feature flags for test context
    settings.RAG_ENABLED = True
    settings.VALIDATION_ENABLED = True

    # 1. Seed Collection and active Validation Policy
    user_id = uuid.UUID("a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d")
    col = KnowledgeCollection(id=uuid.uuid4(), name="Internal Knowledge Base", slug="ikb", owner_id=user_id, status="active")
    db_session.add(col)
    await db_session.commit()

    await policy_service.create_default_policy(db_session)
    
    # 2. Seed Document & Source
    doc = KnowledgeDocument(
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

    db_session.add(doc)
    await db_session.commit()

    source = KnowledgeSource(
        id=uuid.uuid4(),
        document_id=doc.id,
        author="U.S. DOE",
        publisher="Government",
        url="https://www.energy.gov/fusion-economics",
        source_type="government",
        authority_score=1.0
    )
    db_session.add(source)
    await db_session.commit()

    # 3. Seed Chunk with embedding metadata
    chunk = KnowledgeChunk(
        id=uuid.uuid4(),
        document_id=doc.id,
        chunk_number=1,
        character_count=450,
        token_count=100,
        chunk_metadata={
            "content": "Commercial nuclear fusion pilot plant costs are estimated at 5 billion USD. General economics target a levelized cost of electricity below 50 USD per MWh by 2035.",
            "embedding": [0.1] * 1536
        }
    )
    db_session.add(chunk)
    await db_session.commit()

    # 4. Run context preparation
    pkg = await generation_context_service.prepare_context(
        db=db_session,
        query="nuclear fusion pilot plant cost and levelized cost of electricity",
        collection_ids=[col.id],
        user_id=user_id,
        slug="test-slug-1"
    )

    # 5. Asserts
    assert pkg is not None
    assert len(pkg["validated_chunks"]) > 0
    assert pkg["validated_chunks"][0]["text"] == chunk.chunk_metadata["content"]
    assert pkg["knowledge_snapshot_id"] is not None

    # Check that Context Cache is populated by slug key
    cached = await context_cache_service.get_cached_context(db_session, "context:slug:test-slug-1")
    assert cached is not None
    assert cached["knowledge_snapshot_id"] == pkg["knowledge_snapshot_id"]


@pytest.mark.anyio
async def test_ai_gateway_completions_proxy(db_session: AsyncSession):
    # Mock LLM API response from DeepSeek
    mock_llm_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Mocked LLM generation response content."
                }
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150
        }
    }

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = AsyncMock(
            status_code=200,
            json=lambda: mock_llm_response,
            raise_for_status=lambda: None
        )

        res = await ai_gateway_service.chat_completion(
            db=db_session,
            messages=[{"role": "user", "content": "Hello LLM"}],
            model="deepseek-chat",
            slug="test-slug"
        )
        assert res["choices"][0]["message"]["content"] == "Mocked LLM generation response content."


@pytest.mark.anyio
async def test_rag_integration_api_endpoints(db_session: AsyncSession):
    # Enable feature flags for test context
    settings.RAG_ENABLED = True
    settings.VALIDATION_ENABLED = True

    # Seed Collection and active Policy
    user_id = uuid.UUID("a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d")
    col = KnowledgeCollection(id=uuid.uuid4(), name="Internal Knowledge Base", slug="ikb", owner_id=user_id, status="active")
    db_session.add(col)
    await db_session.commit()
    await policy_service.create_default_policy(db_session)

    # Seed Document, Source, Chunk
    doc = KnowledgeDocument(
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

    db_session.add(doc)
    await db_session.commit()

    chunk = KnowledgeChunk(
        id=uuid.uuid4(),
        document_id=doc.id,
        chunk_number=1,
        character_count=450,
        token_count=100,
        chunk_metadata={
            "content": "Commercial nuclear fusion pilot plant costs are estimated at 5 billion USD. General economics target a levelized cost of electricity below 50 USD per MWh by 2035.",
            "embedding": [0.1] * 1536
        }
    )
    db_session.add(chunk)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Preview Context Package
        resp = await ac.get(f"/api/v1/generation/preview-context?query=fusion&collection_ids={col.id}")
        if resp.status_code != 200:
            print("FASTAPI ERROR DETAILS:", resp.json())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["validated_chunks"]) > 0

        # Prepare context by slug to populate cache
        pkg = await generation_context_service.prepare_context(
            db=db_session,
            query="fusion",
            collection_ids=[col.id],
            user_id=user_id,
            slug="report-test-slug"
        )
        snapshot_id = pkg["knowledge_snapshot_id"]

        # 2. Get Snapshot API
        resp = await ac.get(f"/api/v1/generation/snapshots/{snapshot_id}")
        assert resp.status_code == 200
        snap_data = resp.json()["data"]
        assert snap_data["knowledge_version"] == "1.0.0"

        # 3. Get Session details
        session_id = pkg["validation_report_reference"] # wait, the report is saved under ValidationReport matching session_id
        # Let's query the session ID by loading the report
        report_stmt = select(ValidationReport).where(ValidationReport.id == uuid.UUID(session_id))
        report_res = await db_session.execute(report_stmt)
        report = report_res.scalar_one_or_none()
        
        resp = await ac.get(f"/api/v1/generation/sessions/{report.session_id}")
        assert resp.status_code == 200
        sess_data = resp.json()["data"]
        assert sess_data["validation_summary"] is not None

        # 4. Get Analytics API
        resp = await ac.get("/api/v1/generation/analytics")
        assert resp.status_code == 200
        analytics_data = resp.json()["data"]
        assert analytics_data["total_generation_requests"] > 0

        # 5. Get Slug Context
        resp = await ac.get("/api/v1/generation/report-test-slug/context")
        assert resp.status_code == 200
        slug_ctx = resp.json()["data"]
        assert slug_ctx["knowledge_snapshot_id"] == snapshot_id

        # 6. AI Gateway completions API
        mock_llm_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Gateway response"
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        }
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = AsyncMock(
                status_code=200,
                json=lambda: mock_llm_response,
                raise_for_status=lambda: None
            )
            
            resp = await ac.post("/api/v1/aigateway/chat/completions", json={
                "messages": [{"role": "user", "content": "hello"}],
                "model": "deepseek-chat",
                "slug": "report-test-slug"
            }, headers={"x-internal-token": "trusted-worker-secret"})
            assert resp.status_code == 200
            assert resp.json()["choices"][0]["message"]["content"] == "Gateway response"


@pytest.mark.anyio
async def test_selective_context_builder(db_session: AsyncSession):
    from app.services.rag_integration import selective_context_builder
    settings.RAG_ENABLED = True
    settings.VALIDATION_ENABLED = True

    # Seed Collection and Policy
    user_id = uuid.UUID("a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d")
    col = KnowledgeCollection(id=uuid.uuid4(), name="Internal Knowledge Base", slug="ikb", owner_id=user_id, status="active")
    db_session.add(col)
    await db_session.commit()
    await policy_service.create_default_policy(db_session)

    # Seed Document, Source, Chunk
    doc = KnowledgeDocument(
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
    db_session.add(doc)
    await db_session.commit()

    chunk = KnowledgeChunk(
        id=uuid.uuid4(),
        document_id=doc.id,
        chunk_number=1,
        character_count=450,
        token_count=100,
        chunk_metadata={
            "content": "Commercial nuclear fusion pilot plant costs are estimated at 5 billion USD. General economics target a levelized cost of electricity below 50 USD per MWh by 2035.",
            "embedding": [0.1] * 1536
        }
    )
    db_session.add(chunk)
    await db_session.commit()

    pkg = await selective_context_builder.build_context(
        db=db_session,
        query="nuclear fusion pilot plant cost",
        collection_ids=[col.id],
        user_id=user_id,
        slug="partial-test-slug-1"
    )

    assert pkg is not None
    assert len(pkg["validated_chunks"]) > 0
    assert "5 billion USD" in pkg["validated_chunks"][0]["text"]


@pytest.mark.anyio
async def test_partial_regeneration_api(db_session: AsyncSession):
    from app.api.v1.endpoints.reports import MOCK_REPORTS
    from app.models.document import Document
    from app.models.workflow import GenerationJob
    from app.models.enums import JobStatusType
    
    settings.RAG_ENABLED = True
    settings.VALIDATION_ENABLED = True

    # 1. Seed Collection and active Validation Policy
    user_id = uuid.UUID("a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d")
    col = KnowledgeCollection(id=uuid.uuid4(), name="Internal Knowledge Base", slug="ikb", owner_id=user_id, status="active")
    db_session.add(col)
    await db_session.commit()
    await policy_service.create_default_policy(db_session)

    # Seed Document, Source, Chunk
    doc = KnowledgeDocument(
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
    db_session.add(doc)
    
    # Also seed a report document mapping
    report_doc = Document(
        id=uuid.uuid4(),
        title="Nuclear Fusion report",
        slug="nuclear-fusion-report",
        language="en",
        status="draft",
        created_by=user_id
    )
    db_session.add(report_doc)
    await db_session.commit()

    chunk = KnowledgeChunk(
        id=uuid.uuid4(),
        document_id=doc.id,
        chunk_number=1,
        character_count=450,
        token_count=100,
        chunk_metadata={
            "content": "Commercial nuclear fusion pilot plant costs are estimated at 5 billion USD. General economics target a levelized cost of electricity below 50 USD per MWh by 2035.",
            "embedding": [0.1] * 1536
        }
    )
    db_session.add(chunk)
    
    # Add a GenerationJob for the report so we can link it
    job = GenerationJob(
        id=uuid.uuid4(),
        document_id=report_doc.id,
        topic="Nuclear Fusion report",
        status=JobStatusType.completed,
        started=datetime.now(timezone.utc)
    )
    db_session.add(job)
    await db_session.commit()

    # Seed MOCK_REPORTS
    mock_id = str(report_doc.id)
    MOCK_REPORTS[mock_id] = {
        "id": mock_id,
        "title": "Nuclear Fusion report",
        "slug": "nuclear-fusion-report",
        "reportContent": {
            "sections": [
                {
                    "heading": "Executive Summary",
                    "body": "Commercial nuclear fusion pilot plant costs are unknown."
                }
            ]
        }
    }

    # API key mock or set DEEPSEEK_API_KEY to trigger real/mock gateway call
    with patch("httpx.AsyncClient.post") as mock_post:
        # Mock LLM API response from DeepSeek
        mock_llm_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Commercial nuclear fusion pilot plant costs are estimated at 5 billion USD."
                    }
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        }
        mock_post.return_value = AsyncMock(
            status_code=200,
            json=lambda: mock_llm_response,
            raise_for_status=lambda: None
        )

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/api/v1/reports/edit", json={
                    "documentId": mock_id,
                    "action": "regenerate",
                    "paragraphId": "exec-summary-p1",
                    "text": "Commercial nuclear fusion pilot plant costs are unknown."
                })
                
                assert resp.status_code == 200
                data = resp.json()["data"]
                assert "5 billion USD" in data["edited_text"]
                
                # Check that MOCK_REPORTS has been updated
                assert "5 billion USD" in MOCK_REPORTS[mock_id]["reportContent"]["sections"][0]["body"]

                # Check that EvidenceAttribution was logged in the DB
                stmt_attr = select(EvidenceAttribution).where(EvidenceAttribution.generation_job_id == job.id)
                res_attr = await db_session.execute(stmt_attr)
                attributions = res_attr.scalars().all()
                assert len(attributions) > 0
                assert attributions[0].section_id == "exec-summary-p1"
                assert attributions[0].snapshot_id is not None

