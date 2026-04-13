"""
main.py
FastAPI application entrypoint.

Start with:
  uvicorn rfp-intelligence-copilot.main:app --reload --port 8000
Or via Docker:
  docker compose up
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.rfp import router as rfp_router

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan (startup / shutdown hooks) ───────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("RFP Intelligence Copilot API starting up...")
    yield
    logger.info("RFP Intelligence Copilot API shutting down.")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="RFP Intelligence Copilot",
    description="AI-powered RFP parsing, scoring, and supplier matching.",
    version="0.2.0",
    lifespan=lifespan,
)

# Allow the React frontend (dev: localhost:5173, prod: Vercel domain)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://*.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(rfp_router)


# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["Meta"])
def health():
    return {"status": "ok", "version": app.version}
