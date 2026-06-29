import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
import uuid

from app.main import app
from app.core.config import settings
from app.api.deps import get_db
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from app.models.base import Base
from app.models.document import Document
from app.models.identity import User

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(
    TEST_DATABASE_URL, 
    echo=False,
    poolclass=StaticPool,
    connect_args={'check_same_thread': False}
)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)

async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session

app.dependency_overrides[get_db] = override_get_db

@pytest_asyncio.fixture(scope="function")
async def test_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.fixture
def mock_internal_headers():
    return {"x-internal-token": "trusted-worker-secret"}

@pytest.mark.asyncio
async def test_workflow_event_idempotency(test_db, mock_internal_headers):
    # Setup test document
    doc_id = uuid.uuid4()
    async with TestingSessionLocal() as session:
        user = User(id=uuid.uuid4(), full_name="Test", email="test@test.com")
        doc = Document(id=doc_id, title="Test", slug="test", language="en")
        session.add(user)
        session.add(doc)
        await session.commit()

    payload = {
        "document_id": str(doc_id),
        "idempotency_key": f"test-key-{uuid.uuid4()}",
        "metadata": {"source": "github_actions"}
    }
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            f"{settings.API_V1_STR}/internal/events/report-generated",
            json=payload,
            headers=mock_internal_headers
        )
        
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["status"] == "success"
    
    # Test Idempotency
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response2 = await ac.post(
            f"{settings.API_V1_STR}/internal/events/report-generated",
            json=payload,
            headers=mock_internal_headers
        )
        
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["data"]["status"] == "skipped"

@pytest.mark.asyncio
async def test_workflow_event_not_found_rollback(test_db, mock_internal_headers):
    payload = {
        "document_id": str(uuid.uuid4()),
        "idempotency_key": f"test-key-{uuid.uuid4()}",
        "metadata": {}
    }
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            f"{settings.API_V1_STR}/internal/events/report-generated",
            json=payload,
            headers=mock_internal_headers
        )
        
    assert response.status_code == 404
    data = response.json()
    assert data["status"] == "error"
    assert "Not Found" in data["message"] or "Document not found" in data["message"]
