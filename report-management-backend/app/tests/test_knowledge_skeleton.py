import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import settings

@pytest.mark.asyncio
async def test_knowledge_settings():
    assert hasattr(settings, "KNOWLEDGE_ENABLED")
    assert settings.KNOWLEDGE_ENABLED is False
    assert settings.RAG_ENABLED is False
    assert settings.KNOWLEDGE_STORAGE_PROVIDER == "r2"
    assert settings.KNOWLEDGE_VECTOR_PROVIDER == "pgvector"

@pytest.mark.asyncio
async def test_health_check_includes_knowledge():
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
async def test_knowledge_endpoints_disabled_by_default():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Test collections endpoint when disabled
        response = await ac.get(f"{settings.API_V1_STR}/knowledge/collections")
        assert response.status_code == 503
        assert "disabled" in response.json()["message"]

        # Test upload document endpoint when disabled
        response = await ac.post(f"{settings.API_V1_STR}/knowledge/documents?collection_id=00000000-0000-0000-0000-000000000000")
        assert response.status_code == 503

@pytest.mark.asyncio
async def test_knowledge_endpoints_unimplemented_when_enabled(monkeypatch):
    # Enable the flag temporarily to check the 501 stub responses
    monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Collections
        response = await ac.get(f"{settings.API_V1_STR}/knowledge/collections")
        assert response.status_code == 501
        assert "Reserved for future implementation" in response.json()["message"]

        # Search
        response = await ac.post(
            f"{settings.API_V1_STR}/knowledge/search",
            json={"query": "test query", "limit": 5}
        )
        assert response.status_code == 501
