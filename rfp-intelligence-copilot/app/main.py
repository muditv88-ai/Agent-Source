"""
main.py  v4.0

Changes from v3:
  - create_db_and_tables() called in lifespan startup (SQLModel)
  - All 5 routers remain registered; DB is now the persistence layer
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.db import create_db_and_tables
from app.agents.deadline_agent import check_deadlines

from app.api.routes import rfp, chat, communications, suppliers, drawings


# ── Scheduler ──────────────────────────────────────────────────────────────
_scheduler = BackgroundScheduler()
_scheduler.add_job(check_deadlines, "interval", hours=1, id="deadline_check")


# ── Lifespan ───────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    create_db_and_tables()   # create tables if not present (SQLite dev / Postgres prod)
    _scheduler.start()
    yield
    # shutdown
    _scheduler.shutdown(wait=False)


# ── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="RFP Intelligence Copilot",
    version="4.0.0",
    description="AI-powered procurement automation — RFP generation, bid evaluation, supplier onboarding.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files (uploaded drawings)
try:
    app.mount("/static/drawings", StaticFiles(directory="uploads/drawings"), name="drawings")
except RuntimeError:
    pass  # directory may not exist yet on first boot; created by drawings.py

# ── Routers ─────────────────────────────────────────────────────────────────
app.include_router(rfp.router,            prefix="/rfp",            tags=["RFP"])
app.include_router(chat.router,           prefix="/chat",           tags=["Chat"])
app.include_router(communications.router, prefix="/communications", tags=["Communications"])
app.include_router(suppliers.router,      prefix="/suppliers",      tags=["Suppliers"])
app.include_router(drawings.router,       prefix="/drawings",       tags=["Drawings"])


@app.get("/", tags=["Health"])
def health_check():
    return {"status": "ok", "version": "4.0.0", "service": "RFP Intelligence Copilot"}
