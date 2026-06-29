def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "environment": "development"}

def test_placeholder_endpoints(client):
    response = client.get("/api/v1/reports/")
    assert response.status_code == 200
    assert response.json() == {"message": "Not Implemented"}
