import pytest
from httpx import AsyncClient, ASGITransport
import uuid

from app.main import app
from app.core.config import settings

@pytest.fixture
def mock_internal_headers():
    return {"x-internal-token": "trusted-worker-secret"}

@pytest.mark.asyncio
async def test_workflow_event_idempotency(db_session, test_document, mock_internal_headers):
    # Test valid event processing
    payload = {
        "document_id": str(test_document.id),
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
    assert data["data"]["new_state"] == "GENERATED"
    
    # Test Idempotency (Sending the EXACT same payload with same idempotency_key)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response2 = await ac.post(
            f"{settings.API_V1_STR}/internal/events/report-generated",
            json=payload,
            headers=mock_internal_headers
        )
        
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["data"]["status"] == "skipped"
    assert data2["data"]["message"] == "Event already processed"

@pytest.mark.asyncio
async def test_workflow_event_not_found_rollback(mock_internal_headers):
    # Provide a document_id that does NOT exist
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
        
    # WorkflowService raises a 404 HTTPException inside the transaction if document is missing,
    # rolling back any partial changes.
    assert response.status_code == 404
    data = response.json()
    assert data["status"] == "error"
    assert "Document not found" in data["message"]
