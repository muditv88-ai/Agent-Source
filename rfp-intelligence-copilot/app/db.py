"""
Database engine, session factory, and dependency.

Dev:  SQLite  (./rfp_dev.db)    — zero-config, works out of the box
Prod: PostgreSQL                — set DATABASE_URL env var:
        postgresql+psycopg2://<user>:<password>@<host>/<dbname>
"""
from __future__ import annotations

import os
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
_DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "sqlite:///./rfp_dev.db",
)

# SQLite needs check_same_thread=False for FastAPI's async handlers
_connect_args = {"check_same_thread": False} if _DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    _DATABASE_URL,
    echo=False,          # set to True for SQL query logging during dev
    connect_args=_connect_args,
)


# ---------------------------------------------------------------------------
# Table creation (called once at startup from main.py lifespan)
# ---------------------------------------------------------------------------
def create_db_and_tables() -> None:
    """Create all tables defined in app/models/ if they don't exist yet."""
    # Import models so SQLModel metadata is populated before create_all
    import app.models  # noqa: F401
    SQLModel.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
def get_db() -> Generator[Session, None, None]:
    """Yield a SQLModel Session; close it after the request."""
    with Session(engine) as session:
        yield session
