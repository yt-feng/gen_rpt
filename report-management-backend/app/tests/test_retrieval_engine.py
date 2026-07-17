import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, literal
import pytest_asyncio

from app.models.base import Base
from app.models.knowledge import (
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeChunk,
    RetrievalSession,
    RetrievalResult
)
from app.main import app
from app.core.config import settings

# Test database
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession, expire_on_commit=False)

@pytest_asyncio.fixture(scope="function", autouse=True)
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestingSessionLocal() as session:
        from app.database.session import get_db
        async def override_get_db():
            yield session
        app.dependency_overrides[get_db] = override_get_db
        yield session
        
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_retrieval_similarity_math():
    from app.services.retrieval_similarity import calculate_cosine_similarity, calculate_keyword_score
    
    vec1 = [1.0, 0.0, 0.0]
    vec2 = [1.0, 0.0, 0.0]
    assert calculate_cosine_similarity(vec1, vec2) == 1.0
    
    vec3 = [0.0, 1.0, 0.0]
    assert calculate_cosine_similarity(vec1, vec3) == 0.5
    
    assert calculate_keyword_score("hello world", "hello user") == 0.3333333333333333

@pytest.mark.asyncio
async def test_retrieval_ranking_decay():
    from app.services.retrieval_ranking import calculate_freshness_score, calculate_chunk_confidence
    from datetime import datetime, timezone, timedelta
    
    now = datetime.now(timezone.utc)
    assert calculate_freshness_score(now, policy="none") == 1.0
    assert calculate_freshness_score(now - timedelta(days=100), policy="linear") > 0.0
    assert calculate_freshness_score(now - timedelta(days=100), policy="exponential") < 1.0
    
    assert calculate_chunk_confidence(0.8, "validated", 500) == pytest.approx(0.88)
    assert calculate_chunk_confidence(0.8, "flagged", 500) == pytest.approx(0.4)

@pytest.mark.asyncio
async def test_retrieval_end_to_end(db_session, monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_ENABLED", True)
    
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    
    # 1. Setup metadata
    col = KnowledgeCollection(
        id=uuid.uuid4(),
        name="Knowledge Base",
        slug="kb",
        owner_id=user_id,
        organization_id=org_id,
        visibility="private"
    )
    db_session.add(col)
    await db_session.commit()
    
    doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=col.id,
        file_name="report_2025.txt",
        original_file_name="report_2025.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="checksum",
        storage_path="path/1.txt",
        size=1000,
        language="en",
        validation_status="validated",
        processing_status="completed"
    )
    db_session.add(doc)
    await db_session.commit()
    
    from app.services.knowledge_processing.workers.embedding import generate_mock_embedding
    vector = generate_mock_embedding("earnings growth")
    
    chunk = KnowledgeChunk(
        id=uuid.uuid4(),
        document_id=doc.id,
        chunk_number=1,
        embedding=vector,
        chunk_metadata={
            "content": "earnings growth",
            "embedding": vector
        }
    )
    db_session.add(chunk)
    await db_session.commit()
    
    async def override_user():
        return {
            "id": str(user_id),
            "organization_id": str(org_id),
            "role": "owner"
        }
    from app.api.deps import get_current_user_placeholder
    app.dependency_overrides[get_current_user_placeholder] = override_user
    
    # 2. Invoke API
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            f"{settings.API_V1_STR}/knowledge/retrieval/query",
            json={
                "topic": "earnings growth",
                "target_count": 5,
                "collection_ids": [str(col.id)]
            }
        )
        assert response.status_code == 200
        data = response.json()["data"]
        
        assert "earnings growth" in data["context"]
        assert len(data["chunks"]) == 1
        assert data["chunks"][0]["similarity_score"] > 0.5
        
        session_id = data["session_id"]
        
        hist_resp = await ac.get(f"{settings.API_V1_STR}/knowledge/retrieval/session/{session_id}")
        assert hist_resp.status_code == 200
        hist_data = hist_resp.json()["data"]
        assert hist_data["session"]["query"] == "earnings growth"
        assert len(hist_data["results"]) == 1
        
    app.dependency_overrides.clear()
