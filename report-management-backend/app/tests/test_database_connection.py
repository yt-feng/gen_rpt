import os
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from app.core.config import Settings
from app.core.database import engine, AsyncSessionLocal
from app.main import app
import pytest_asyncio

@pytest.mark.asyncio
async def test_database_url_loads():
    from app.core.config import settings
    assert settings.DATABASE_URL is not None

def test_invalid_database_url_fails():
    old_val = os.environ.get("DATABASE_URL")
    if "DATABASE_URL" in os.environ:
        del os.environ["DATABASE_URL"]
    
    with pytest.raises(ValueError):
        Settings(_env_file=None)
        
    if old_val is not None:
        os.environ["DATABASE_URL"] = old_val

@pytest.mark.asyncio
async def test_sqlalchemy_engine_initializes():
    assert engine is not None
    assert engine.url is not None

@pytest.mark.asyncio
async def test_session_creation_succeeds():
    async with AsyncSessionLocal() as session:
        assert session is not None
        assert session.is_active

@pytest.mark.asyncio
async def test_database_connection_succeeds():
    # Test the real Supabase connection using the engine configured in settings
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1

@pytest.mark.asyncio
async def test_rollback_succeeds():
    async with AsyncSessionLocal() as session:
        await session.rollback()
        assert session.is_active

@pytest.mark.asyncio
async def test_health_endpoint():
    # We mock the engine for the app in the health check route by patching or just 
    # relying on the app catching the connection error gracefully.
    # If the real DB is down, it returns "degraded" which is a valid response format.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
        
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "database" in data
    assert "response_time_ms" in data["database"]
