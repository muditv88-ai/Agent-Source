"""
app/db_core.py  —  Canonical database engine (SQLModel-based).

This file is the renamed version of the original app/db.py.
It exists because app/db/ is now a package (directory), and Python
cannot have both app/db.py and app/db/ at the same time — the package
wins and the flat file is silently ignored, breaking all imports.

Fix: the original db.py content lives here as db_core.py.
app/db/__init__.py re-exports everything from this file.

Dev:  SQLite  (DATA_DIR/sourceiq.db)    — zero-config
Prod: PostgreSQL                        — set DATABASE_URL env var:
        postgresql+psycopg2://<user>:<password>@<host>/<dbname>
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine


# ── Persistent database path (mirrors project_store DATA_DIR logic) ────────

def _resolve_db_url() -> str:
    """Return DATABASE_URL from env, or build an absolute SQLite path."""
    env_url = os.environ.get("DATABASE_URL", "").strip()
    if env_url:
        return env_url

    # Mirror the DATA_DIR resolution from project_store so the SQLite
    # file lands next to the projects/ directory — both on a persistent volume.
    env_data = os.environ.get("DATA_DIR", "").strip()
    if env_data:
        data_dir = Path(env_data).resolve()
    elif Path("/app/data").parent.exists():  # inside Docker / Railway
        data_dir = Path("/app/data")
    else:
        data_dir = Path("data").resolve()    # local dev

    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{data_dir / 'sourceiq.db'}"


_DATABASE_URL: str = _resolve_db_url()

_connect_args = {"check_same_thread": False} if _DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    _DATABASE_URL,
    echo=False,
    connect_args=_connect_args,
)

SessionLocal = Session  # alias for code that imports SessionLocal from app.db


# ── Table creation ────────────────────────────────────────────────────────────

def create_db_and_tables() -> None:
    """Create all SQLModel tables if they do not already exist."""
    import app.models  # noqa: F401  — populates SQLModel.metadata
    SQLModel.metadata.create_all(engine)
    print(f"[db_core] Tables ensured in: {_DATABASE_URL}")


# ── FastAPI dependency ────────────────────────────────────────────────────────

Base = SQLModel  # For code that does `from app.db import Base`


def get_db() -> Generator[Session, None, None]:
    """Yield a SQLModel Session; close it after the request."""
    with Session(engine) as session:
        yield session
