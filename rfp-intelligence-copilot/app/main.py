"""
main.py  v3.0

Version bump: 2.0.0 → 3.0.0

Changes from v2:
  - Added APScheduler deadline agent (runs every hour)
  - Registered /suppliers router (SupplierOnboardingAgent-backed)
  - Registered /drawings  router (RFPGenerationAgent-backed)
  - Static file mount for /static/drawings (uploaded drawing files)
  - Version bump and updated module listing

All v2.0 routes preserved for full backward compatibility.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes.health         import router as health_router
from app.api.routes.auth           import router as auth_router
from app.api.routes.rfp            import router as rfp_router
from app.api.routes.analysis       import router as analysis_router
from app.api.routes.scenarios      import router as scenarios_router
from app.api.routes.communications import router as communications_router
from app.api.routes.chat           import router as chat_router
from app.api.routes.pricing        import router as pricing_router
from app.api.routes.projects       import router as projects_router
from app.api.routes.suppliers      import router as suppliers_router   # NEW v3.0
from app.api.routes.drawings       import router as drawings_router     # NEW v3.0

logger = logging.getLogger("sourceiq")


# ── APScheduler: deadline monitoring ─────────────────────────────────────
def _start_deadline_scheduler():
    """Start the background deadline reminder scheduler."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from app.agents.deadline_agent import check_deadlines

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            check_deadlines,
            trigger="interval",
            hours=1,
            id="deadline_check",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("[v3.0] Deadline scheduler started — runs every hour.")
        return scheduler
    except ImportError as e:
        logger.warning(
            f"[v3.0] APScheduler not installed — deadline reminders disabled. "
            f"Install with: pip install apscheduler  ({e})"
        )
        return None
    except Exception as e:
        logger.error(f"[v3.0] Failed to start deadline scheduler: {e}")
        return None


# ── App lifespan (startup / shutdown) ─────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: launch scheduler. Shutdown: stop it cleanly."""
    scheduler = _start_deadline_scheduler()
    yield
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[v3.0] Deadline scheduler stopped.")


# ── App factory ─────────────────────────────────────────────────────────
app = FastAPI(
    title="SourceIQ",
    version="3.0.0",
    description=(
        "SourceIQ — Procurement Intelligence Platform. "
        "Modules: Projects, RFP Generation, Supplier Management, "
        "Technical Analysis, Pricing Analysis, Award Scenarios, "
        "Communications, Chatbot Copilot."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files (drawing uploads) ───────────────────────────────────────
import os
os.makedirs("uploads/drawings", exist_ok=True)
app.mount("/static/drawings", StaticFiles(directory="uploads/drawings"), name="drawings")


# ── Core routers (UNCHANGED paths — full backward compat) ─────────────────
app.include_router(health_router)
app.include_router(auth_router,           prefix="/auth",           tags=["Auth"])
app.include_router(projects_router,       prefix="/projects",       tags=["Projects"])
app.include_router(rfp_router,            prefix="/rfp",            tags=["RFP"])
app.include_router(scenarios_router,      prefix="/scenarios",      tags=["Scenarios"])
app.include_router(communications_router, prefix="/communications", tags=["Communications"])
app.include_router(chat_router,           prefix="/chat",           tags=["Chat"])

# ── Analysis: original path + v2.0 alias (UNCHANGED) ──────────────────────
app.include_router(
    analysis_router,
    prefix="/analysis",
    tags=["Analysis"],
)
app.include_router(
    analysis_router,
    prefix="/technical-analysis",
    tags=["Technical Analysis"],
)

# ── Pricing: original path + v2.0 alias (UNCHANGED) ───────────────────────
app.include_router(
    pricing_router,
    prefix="/pricing",
    tags=["Pricing"],
)
app.include_router(
    pricing_router,
    prefix="/pricing-analysis",
    tags=["Pricing Analysis"],
)

# ── NEW v3.0 routers ──────────────────────────────────────────────────────
app.include_router(suppliers_router, prefix="/suppliers", tags=["Suppliers"])
app.include_router(drawings_router,  prefix="/drawings",  tags=["Drawings"])


@app.get("/")
def root():
    return {
        "message": "SourceIQ API is running",
        "version": "3.0.0",
        "modules": [
            "Projects                  /projects",
            "RFP (+ AI generation)     /rfp",
            "Technical Analysis        /analysis  | /technical-analysis",
            "Pricing Analysis          /pricing   | /pricing-analysis",
            "Award Scenarios           /scenarios",
            "Communications            /communications  (CommsAgent)",
            "Chat / Copilot            /chat            (CopilotAgent)",
            "Suppliers + Onboarding    /suppliers       (NEW v3.0)",
            "Drawings                  /drawings        (NEW v3.0)",
        ],
        "agents": [
            "BaseAgent", "OrchestratorAgent",
            "RFPGenerationAgent", "SupplierOnboardingAgent",
            "ResponseIntakeAgent", "CommsAgent",
            "DeadlineAgent (scheduler)", "TechnicalAnalysisAgent",
            "PricingAgent", "AwardAgent", "CopilotAgent",
        ],
    }
