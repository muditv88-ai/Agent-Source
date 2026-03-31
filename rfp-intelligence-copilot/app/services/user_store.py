"""
File-based user store. Users stored in users.json.

CLI usage:
  python -c "from app.services.user_store import create_user; create_user('alice', 'pass123', 'user')"
"""
import json
import os
import re
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


def _slug(name: str) -> str:
    """Turn an email or display name into a safe username."""
    base = name.split("@")[0]
    return re.sub(r"[^a-zA-Z0-9_-]", "_", base)[:32]


def create_user(
    username: str,
    password: str = "",
    role: str = "user",
    email: str = "",
    google_email: str = "",
) -> dict:
    data = _load()
    if username in data:
        raise ValueError(f"Username '{username}' is already taken")
    entry: dict = {"role": role}
    if password:
        entry["password_hash"] = pwd_context.hash(password)
    if email:
        entry["email"] = email
    if google_email:
        entry["google_email"] = google_email
    data[username] = entry
    _save(data)
    return {"username": username, "role": role}


def get_or_create_google_user(google_email: str, display_name: str) -> dict:
    """Find existing user by google_email or create one on first login."""
    data = _load()
    # Look for existing Google user
    for username, entry in data.items():
        if entry.get("google_email") == google_email:
            return {"username": username, "role": entry["role"]}
    # First-time Google login — auto-create account
    base = _slug(display_name or google_email)
    username = base
    counter = 1
    while username in data:
        username = f"{base}{counter}"
        counter += 1
    data[username] = {
        "role": "user",
        "google_email": google_email,
        "email": google_email,
    }
    _save(data)
    return {"username": username, "role": "user"}


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
    return [
        {"username": u, "role": v["role"], "email": v.get("email", "")}
        for u, v in data.items()
    ]


def authenticate(username: str, password: str) -> dict | None:
    data = _load()
    user = data.get(username)
    if not user:
        return None
    pw_hash = user.get("password_hash")
    if not pw_hash:
        return None  # Google-only account, no password set
    if not pwd_context.verify(password, pw_hash):
        return None
    return {"username": username, "role": user["role"]}


def get_user(username: str) -> dict | None:
    data = _load()
    user = data.get(username)
    if not user:
        return None
    return {"username": username, "role": user["role"]}
