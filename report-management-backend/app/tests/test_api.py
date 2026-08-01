import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import settings
from app.api.v1.endpoints import reports

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


@pytest.mark.asyncio
async def test_report_details_replaces_expired_image_urls(monkeypatch):
    report_id = "image-refresh-test"
    reports.MOCK_REPORTS[report_id] = {
        "id": report_id,
        "slug": report_id,
        "assignedTo": {"id": "test"},
        "reportContent": {"images": [{"key": "image-1.png", "url": "expired"}]},
    }

    class FakeS3:
        def list_objects_v2(self, **_kwargs):
            return {"Contents": [{"Key": f"reports/{report_id}/current/assets/image-1.png"}]}

        def generate_presigned_url(self, **_kwargs):
            return "fresh"

    from app.storage.provider import storage_provider
    monkeypatch.setattr(storage_provider, "s3_client", FakeS3())
    try:
        response = await reports.get_report_details(report_id, db=None, user={})
        assert response.data["reportContent"]["images"][0]["url"] == "fresh"
    finally:
        reports.MOCK_REPORTS.pop(report_id, None)

