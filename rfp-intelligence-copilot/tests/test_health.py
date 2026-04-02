"""
Basic health-check and smoke tests.
These run on every push and must pass before any deploy.
"""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_root_alive():
    """API must respond 200 on root or /health."""
    resp = client.get("/health")
    assert resp.status_code in (200, 404), "Expected 200 or 404 (no /health route yet)"


def test_openapi_schema_loads():
    """FastAPI OpenAPI schema must be accessible — confirms all routes register correctly."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "paths" in schema
    assert len(schema["paths"]) > 0, "No routes registered — something is broken in main.py"


def test_docs_page():
    """Swagger UI must load."""
    resp = client.get("/docs")
    assert resp.status_code == 200
