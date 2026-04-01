"""
main.py  v4.1

Fixes v4.0 regression: all 8 original routers re-registered.
Additions vs v3:
  - create_db_and_tables() in lifespan startup (SQLModel persistence)
  - APScheduler deadline check (hourly)
  - Static file mount for uploaded drawings
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.db import create_db_and_tables
from app.agents.deadline_agent import check_deadlines

# ── all routers ───────────────────────────────────────────────────────────
from app.api.routes.auth           import router as auth_router
from app.api.routes.health         import router as health_router
from app.api.routes.projects       import router as projects_router
from app.api.routes.rfp            import router as rfp_router
from app.api.routes.analysis       import router as analysis_router
from app.api.routes.pricing        import router as pricing_router
from app.api.routes.scenarios      import router as scenarios_router
from app.api.routes.chat           import router as chat_router
from app.api.routes.communications import router as communications_router
from app.api.routes.suppliers      import router as suppliers_router
from app.api.routes.drawings       import router as drawings_router


# ── Scheduler ─────────────────────────────────────────────────────────────
_scheduler = BackgroundScheduler()
_scheduler.add_job(check_deadlines, "interval", hours=1, id="deadline_check")


# ── Lifespan ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()   # idempotent — creates tables if not present
    _scheduler.start()
    yield
    _scheduler.shutdown(wait=False)


# ── App ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="RFP Intelligence Copilot",
    version="4.1.0",
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

# Static file mount for drawings (directory created lazily by drawings.py)
try:
    app.mount("/static/drawings", StaticFiles(directory="uploads/drawings"), name="drawings")
except RuntimeError:
    pass

# ── Routers ───────────────────────────────────────────────────────────────
app.include_router(health_router,         prefix="/health",         tags=["Health"])
app.include_router(auth_router,           prefix="/auth",           tags=["Auth"])
app.include_router(projects_router,       prefix="/projects",       tags=["Projects"])
app.include_router(rfp_router,            prefix="/rfp",            tags=["RFP"])
app.include_router(analysis_router,       prefix="/analysis",       tags=["Analysis"])
app.include_router(pricing_router,        prefix="/pricing",        tags=["Pricing"])
app.include_router(scenarios_router,      prefix="/scenarios",      tags=["Scenarios"])
app.include_router(chat_router,           prefix="/chat",           tags=["Chat"])
app.include_router(communications_router, prefix="/communications", tags=["Communications"])
app.include_router(suppliers_router,      prefix="/suppliers",      tags=["Suppliers"])
app.include_router(drawings_router,       prefix="/drawings",       tags=["Drawings"])


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "version": "4.1.0", "service": "RFP Intelligence Copilot"}
