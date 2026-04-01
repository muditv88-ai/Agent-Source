"""
app/db  —  Database layer

Provides:
  - get_db()     : FastAPI dependency for SQLAlchemy sessions
  - Base        : declarative base for all ORM models
  - engine      : SQLAlchemy engine (configured via DATABASE_URL env var)

Backward-compatible: the old flat app/db.py is superseded by this package.
Anything that imported from app.db still works via the re-exports below.
"""
from app.db.session import engine, get_db, SessionLocal  # noqa: F401
from app.db.models import Base  # noqa: F401

__all__ = ["engine", "get_db", "SessionLocal", "Base"]
