"""
main.py  v2.0

Version bump: 1.2.0 → 2.0.0

Changes from v1:
  - /technical-analysis  alias added (same router as /analysis)
  - /pricing-analysis    alias added (same router as /pricing)
  - Both original paths (/analysis, /pricing) preserved for full backward compat
  - App title updated to SourceIQ
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.health         import router as health_router
from app.api.routes.auth           import router as auth_router
from app.api.routes.rfp            import router as rfp_router
from app.api.routes.analysis       import router as analysis_router
from app.api.routes.scenarios      import router as scenarios_router
from app.api.routes.communications import router as communications_router
from app.api.routes.chat           import router as chat_router
from app.api.routes.pricing        import router as pricing_router
from app.api.routes.projects       import router as projects_router

app = FastAPI(
    title="SourceIQ",
    version="2.0.0",
    description=(
        "SourceIQ — Procurement Intelligence Platform. "
        "Modules: Projects, RFP, Technical Analysis, Pricing Analysis, Chatbot."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Core routers (UNCHANGED paths — full backward compat) ─────────────────
app.include_router(health_router)
app.include_router(auth_router,           prefix="/auth",           tags=["Auth"])
app.include_router(projects_router,       prefix="/projects",       tags=["Projects"])
app.include_router(rfp_router,            prefix="/rfp",            tags=["RFP"])
app.include_router(scenarios_router,      prefix="/scenarios",      tags=["Scenarios"])
app.include_router(communications_router, prefix="/communications", tags=["Communications"])
app.include_router(chat_router,           prefix="/chat",           tags=["Chat"])

# ── Analysis: original path + v2.0 alias ──────────────────────────────────
app.include_router(
    analysis_router,
    prefix="/analysis",
    tags=["Analysis"],                     # kept for backward compat
)
app.include_router(
    analysis_router,
    prefix="/technical-analysis",
    tags=["Technical Analysis"],           # v2.0 canonical name
)

# ── Pricing: original path + v2.0 alias ───────────────────────────────────
app.include_router(
    pricing_router,
    prefix="/pricing",
    tags=["Pricing"],                      # kept for backward compat
)
app.include_router(
    pricing_router,
    prefix="/pricing-analysis",
    tags=["Pricing Analysis"],             # v2.0 canonical name
)


@app.get("/")
def root():
    return {
        "message": "SourceIQ API is running",
        "version": "2.0.0",
        "modules": [
            "Projects",
            "RFP",
            "Technical Analysis  (/analysis  | /technical-analysis)",
            "Pricing Analysis    (/pricing   | /pricing-analysis)",
            "Chat",
        ],
    }
