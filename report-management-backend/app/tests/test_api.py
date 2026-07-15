import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import settings

@pytest.mark.asyncio
async def test_api_standard_response():
    # Calling an endpoint that is just a placeholder to test response formatting
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(f"{settings.API_V1_STR}/dashboard/metrics")
        
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "data" in data
    assert "pending_reviews" in data["data"]
    assert "request_id" in data
    assert "timestamp" in data

@pytest.mark.asyncio
async def test_api_404_error():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(f"{settings.API_V1_STR}/does_not_exist")
        
    assert response.status_code == 404
    data = response.json()
    assert data["status"] == "error"
    assert "message" in data
    assert "Not Found" in data["message"]

@pytest.mark.asyncio
async def test_api_validation_error():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Invalid UUID format should trigger validation error
        response = await ac.get(f"{settings.API_V1_STR}/reports/invalid-uuid/versions")
        
    assert response.status_code == 422
    data = response.json()
    assert data["status"] == "error"
    assert data["message"] == "Validation error"
    assert "errors" in data
    assert len(data["errors"]) > 0

