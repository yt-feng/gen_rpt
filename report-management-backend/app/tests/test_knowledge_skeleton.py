import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import pytest_asyncio

from app.models.base import Base
from app.main import app
from app.core.config import settings

# Use SQLite memory for database test context
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession, expire_on_commit=False)

@pytest_asyncio.fixture(scope="function", autouse=True)
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestingSessionLocal() as session:
        # Override FastAPI dependency get_db with TestingSessionLocal
        from app.database.session import get_db
        async def override_get_db():
            yield session
        app.dependency_overrides[get_db] = override_get_db
        yield session
        
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_knowledge_settings():
    assert hasattr(settings, "KNOWLEDGE_ENABLED")
    assert settings.KNOWLEDGE_STORAGE_PROVIDER == "r2"
    assert settings.KNOWLEDGE_VECTOR_PROVIDER == "pgvector"

@pytest.mark.asyncio
async def test_health_check_includes_knowledge(monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    
    assert response.status_code == 200
    data = response.json()
    assert "knowledge" in data
    k_health = data["knowledge"]
    assert k_health["status"] == "idle"
    assert k_health["module_loaded"] is True
    assert k_health["feature_flags"]["KNOWLEDGE_ENABLED"] is False

@pytest.mark.asyncio
async def test_knowledge_endpoints_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Test collections endpoint when disabled
        response = await ac.get(f"{settings.API_V1_STR}/knowledge/collections")
        assert response.status_code == 503
        assert "disabled" in response.json()["message"]

        # Test upload document endpoint when disabled
        response = await ac.post(f"{settings.API_V1_STR}/knowledge/documents/upload?collection_id=00000000-0000-0000-0000-000000000000")
        assert response.status_code == 503

# (All stubs are now fully implemented in Phase R6 and R7)
