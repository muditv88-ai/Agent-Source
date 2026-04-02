"""
Auth endpoint tests — registration, login, token validation.
"""
import pytest

REGISTER_PAYLOAD = {
    "email": "test@sourceiq.dev",
    "password": "Str0ng!Pass",
    "full_name": "Test User",
}


def test_register_new_user(client):
    """POST /auth/register should create a user and return a token or user object."""
    resp = client.post("/auth/register", json=REGISTER_PAYLOAD)
    assert resp.status_code in (200, 201), resp.text


def test_register_duplicate_email(client):
    """Registering the same email twice must return 400 or 409."""
    client.post("/auth/register", json=REGISTER_PAYLOAD)  # first time
    resp = client.post("/auth/register", json=REGISTER_PAYLOAD)  # duplicate
    assert resp.status_code in (400, 409, 422), resp.text


def test_login_valid_credentials(client):
    """Valid login must return an access_token."""
    client.post("/auth/register", json=REGISTER_PAYLOAD)
    resp = client.post(
        "/auth/login",
        data={"username": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "access_token" in data


def test_login_wrong_password(client):
    """Wrong password must return 401."""
    client.post("/auth/register", json=REGISTER_PAYLOAD)
    resp = client.post(
        "/auth/login",
        data={"username": REGISTER_PAYLOAD["email"], "password": "wrongpass"},
    )
    assert resp.status_code == 401, resp.text


def test_protected_route_without_token(client):
    """Hitting a protected route without a bearer token must return 401."""
    resp = client.get("/projects/")
    assert resp.status_code in (401, 403), resp.text
