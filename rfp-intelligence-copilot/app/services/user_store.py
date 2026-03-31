"""
Simple file-based user store.
Users are stored in users.json as:
  {"username": {"password_hash": "...", "role": "admin|user"}}

To add a user from CLI:
  python -c "from app.services.user_store import create_user; create_user('alice', 'secret123', 'user')"
"""
import json
import os
from pathlib import Path
from passlib.context import CryptContext

USERS_FILE = Path(os.environ.get("USERS_FILE", "users.json"))
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def _load() -> dict:
    if not USERS_FILE.exists():
        return {}
    return json.loads(USERS_FILE.read_text())


def _save(data: dict):
    USERS_FILE.write_text(json.dumps(data, indent=2))


def create_user(username: str, password: str, role: str = "user") -> dict:
    data = _load()
    if username in data:
        raise ValueError(f"User '{username}' already exists")
    data[username] = {
        "password_hash": pwd_context.hash(password),
        "role": role,
    }
    _save(data)
    return {"username": username, "role": role}


def update_password(username: str, new_password: str):
    data = _load()
    if username not in data:
        raise ValueError(f"User '{username}' not found")
    data[username]["password_hash"] = pwd_context.hash(new_password)
    _save(data)


def delete_user(username: str):
    data = _load()
    if username not in data:
        raise ValueError(f"User '{username}' not found")
    del data[username]
    _save(data)


def list_users() -> list:
    data = _load()
    return [{"username": u, "role": v["role"]} for u, v in data.items()]


def authenticate(username: str, password: str) -> dict | None:
    """Returns user dict if credentials valid, else None."""
    data = _load()
    user = data.get(username)
    if not user:
        return None
    if not pwd_context.verify(password, user["password_hash"]):
        return None
    return {"username": username, "role": user["role"]}


def get_user(username: str) -> dict | None:
    data = _load()
    user = data.get(username)
    if not user:
        return None
    return {"username": username, "role": user["role"]}
