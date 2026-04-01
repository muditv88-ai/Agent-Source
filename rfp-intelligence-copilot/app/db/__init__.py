"""
app/db  —  Database layer

This package re-exports everything that the old flat app/db.py provided
so that all existing imports remain backward-compatible:

    from app.db import engine, get_db, SessionLocal   # ✔ still works
    from app.db import create_db_and_tables           # ✔ now works (was broken)
    from app.db import Base                            # ✔ still works

The canonical implementation lives in app/db.py (SQLModel-based).
The session.py / models.py sub-modules are for the raw SQLAlchemy layer
used by the storage abstraction.
"""
# Re-export everything from the flat db.py that main.py and other
# modules depend on.  Python resolves `app.db` to this package, so we
# must forward every public symbol from the original file.
from app.db_core import (  # noqa: F401
    engine,
    get_db,
    SessionLocal,
    create_db_and_tables,
)

# SQLModel Base (needed by any module that does `from app.db import Base`)
try:
    from app.db_core import Base  # noqa: F401
except ImportError:
    pass

__all__ = ["engine", "get_db", "SessionLocal", "create_db_and_tables", "Base"]
