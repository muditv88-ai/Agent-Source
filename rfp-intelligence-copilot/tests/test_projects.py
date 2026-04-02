"""
Project CRUD endpoint tests.
"""
import pytest

_TOKEN = None


def _get_token(client) -> str:
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    client.post(
        "/auth/register",
        json={"email": "proj@sourceiq.dev", "password": "Str0ng!Pass", "full_name": "Proj User"},
    )
    resp = client.post(
        "/auth/login",
        data={"username": "proj@sourceiq.dev", "password": "Str0ng!Pass"},
    )
    _TOKEN = resp.json().get("access_token", "")
    return _TOKEN


def auth_headers(client) -> dict:
    return {"Authorization": f"Bearer {_get_token(client)}"}


def test_list_projects_empty(client):
    resp = client.get("/projects/", headers=auth_headers(client))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_create_project(client):
    payload = {"name": "Test RFP Project", "description": "Automated test project"}
    resp = client.post("/projects/", json=payload, headers=auth_headers(client))
    assert resp.status_code in (200, 201), resp.text
    data = resp.json()
    assert data.get("name") == payload["name"]


def test_create_project_missing_name(client):
    resp = client.post("/projects/", json={}, headers=auth_headers(client))
    assert resp.status_code == 422  # Pydantic validation error


def test_get_project_not_found(client):
    resp = client.get("/projects/99999", headers=auth_headers(client))
    assert resp.status_code == 404
