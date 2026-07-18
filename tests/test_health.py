"""Contract test for the health endpoint (US2)."""

from fastapi.testclient import TestClient

from app.core.config import get_settings


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": get_settings().app_name}
