def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "healthy"
    assert data.get("environment") == "development"

def test_reports_endpoint(client):
    response = client.get("/api/v1/reports/")
    assert response.status_code == 200
    data = response.json()
    assert "data" in data or "reports" in data or isinstance(data, list) or data.get("success") is True

