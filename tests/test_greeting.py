"""Contract tests for the greeting endpoint (US1) and unknown-path handling (FR-005)."""

from fastapi.testclient import TestClient


def test_greeting_returns_hello_world(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "hello world", "success": True}


def test_unknown_path_returns_404(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
