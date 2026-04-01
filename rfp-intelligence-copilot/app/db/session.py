"""
app/db/session.py  —  SQLAlchemy engine + session factory

Supports both SQLite (local dev) and PostgreSQL / Supabase (production).
Set DATABASE_URL environment variable to switch:
  - SQLite (default):     sqlite:///./sourceiq.db
  - PostgreSQL/Supabase:  postgresql+psycopg2://<user>:<pass>@<host>/<db>
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL: str = os.environ.get(
    "DATABASE_URL", "sqlite:///./sourceiq.db"
)

# SQLite needs check_same_thread=False; other DBs ignore this kwarg
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,  # reconnect if stale connection
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency — yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
