"""
main.py  v5.0

Adds:
  - /files router  — GCS-backed persistent project file library
    (RFP templates + supplier responses stored once, reusable)
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from app.db import create_db_and_tables
from app.agents.deadline_agent import start_deadline_scheduler, stop_deadline_scheduler

# ── all routers ────────────────────────────────────────────────────────
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
from app.api.routes.files          import router as files_router


# ── Lifespan ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()       # idempotent — creates tables if not present
    start_deadline_scheduler()   # starts APScheduler hourly deadline check
    yield
    stop_deadline_scheduler()    # clean shutdown


# ── CORS origins ─────────────────────────────────────────────────────
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]


# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="RFP Intelligence Copilot",
    version="5.0.0",
    description="AI-powered procurement automation — RFP generation, bid evaluation, supplier onboarding, persistent GCS file library.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static file mount for drawings (directory created lazily by drawings.py)
try:
    app.mount("/static/drawings", StaticFiles(directory="uploads/drawings"), name="drawings")
except RuntimeError:
    pass

# ── Routers ──────────────────────────────────────────────────────────
app.include_router(health_router,         prefix="/health",             tags=["Health"])
app.include_router(auth_router,           prefix="/auth",               tags=["Auth"])
app.include_router(projects_router,       prefix="/projects",           tags=["Projects"])
app.include_router(rfp_router,            prefix="/rfp",                tags=["RFP"])
app.include_router(analysis_router,       prefix="/technical-analysis", tags=["Analysis"])
app.include_router(pricing_router,        prefix="/pricing-analysis",   tags=["Pricing"])
app.include_router(scenarios_router,      prefix="/scenarios",          tags=["Scenarios"])
app.include_router(chat_router,           prefix="/chat",               tags=["Chat"])
app.include_router(communications_router, prefix="/communications",     tags=["Communications"])
app.include_router(suppliers_router,      prefix="/suppliers",          tags=["Suppliers"])
app.include_router(drawings_router,       prefix="/drawings",           tags=["Drawings"])
app.include_router(files_router,          prefix="/files",              tags=["Files"])


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "version": "5.0.0", "service": "RFP Intelligence Copilot"}
