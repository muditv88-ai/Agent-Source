"""
pricing.py  — Pricing Agent routes  (v2: push_log instrumentation)
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_db
from app.api.routes.agent_logs import push_log

router = APIRouter(tags=["Pricing"])

try:
    from app.agents.pricing_agent import PricingAgent
    _PRICING_AGENT_AVAILABLE = True
except ImportError:
    _PRICING_AGENT_AVAILABLE = False

try:
    from app.services import gcs_storage as _gcs
    _GCS_ENABLED = True
except Exception:
    _GCS_ENABLED = False


def _save_to_gcs(project_id, category, filename, file_bytes, content_type):
    if not _GCS_ENABLED:
        return None
    try:
        return _gcs.upload_file(
            project_id=project_id or "unassigned",
            category=category,
            filename=filename,
            file_bytes=file_bytes,
            content_type=content_type or "application/octet-stream",
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("GCS upload failed: %s", exc)
        return None


class PricingAnalyzeRequest(BaseModel):
    project_id: str
    supplier_prices: Dict[str, Any]
    line_items: Optional[List[str]] = None
    context: Optional[str] = ""


class MarketRateRequest(BaseModel):
    category: str
    line_items: Optional[List[str]] = None
    context: Optional[str] = ""


@router.post("/analyze")
async def analyze_pricing(payload: PricingAnalyzeRequest):
    """
    Run market-rate pricing analysis across supplier quotes.
    """
    push_log(agent_id="pricing", status="running",
             message=f"Analysing supplier pricing for project {payload.project_id}")
    if not _PRICING_AGENT_AVAILABLE:
        push_log(agent_id="pricing", status="error",
                 message="PricingAgent not available — check backend dependencies")
        raise HTTPException(503, detail="PricingAgent not available")

    try:
        t0    = time.time()
        agent = PricingAgent()
        result = agent.run({
            "project_id":      payload.project_id,
            "supplier_prices": payload.supplier_prices,
            "line_items":      payload.line_items or [],
            "context":         payload.context or "",
        })
        comparable_count = result.get("comparable_rfps_analysed", 0)
        push_log(agent_id="pricing", status="complete",
                 message=f"Analysed {comparable_count} comparable RFPs",
                 confidence=81,
                 duration_ms=int((time.time() - t0) * 1000))
        return result
    except Exception as e:
        push_log(agent_id="pricing", status="error",
                 message=f"Pricing analysis failed: {e}")
        raise HTTPException(500, detail=str(e))


@router.post("/market-rates")
async def get_market_rates(payload: MarketRateRequest):
    """
    Estimate market-rate ranges for a given procurement category.
    """
    push_log(agent_id="pricing", status="running",
             message=f"Fetching market rates for {payload.category}")
    if not _PRICING_AGENT_AVAILABLE:
        push_log(agent_id="pricing", status="error",
                 message="PricingAgent not available")
        raise HTTPException(503, detail="PricingAgent not available")

    try:
        t0     = time.time()
        agent  = PricingAgent()
        result = agent.get_market_rates({
            "category":   payload.category,
            "line_items": payload.line_items or [],
            "context":    payload.context or "",
        })
        push_log(agent_id="pricing", status="complete",
                 message=f"Market rates retrieved for {payload.category}",
                 confidence=81,
                 duration_ms=int((time.time() - t0) * 1000))
        return result
    except Exception as e:
        push_log(agent_id="pricing", status="error", message=str(e))
        raise HTTPException(500, detail=str(e))


@router.post("/ingest")
async def ingest_pricing_file(
    file:       UploadFile     = File(...),
    project_id: Optional[str] = Form(None),
):
    """
    Accept an xlsx/csv of supplier pricing and store it.
    """
    push_log(agent_id="pricing", status="running",
             message=f"Ingesting pricing file: {file.filename}")
    file_bytes = await file.read()

    gcs_blob = _save_to_gcs(
        project_id=project_id,
        category="pricing_files",
        filename=file.filename,
        file_bytes=file_bytes,
        content_type=file.content_type,
    )

    push_log(agent_id="pricing", status="complete",
             message=f"Pricing file stored: {file.filename}",
             confidence=81)
    return {
        "status":     "received",
        "filename":   file.filename,
        "project_id": project_id,
        "gcs_blob":   gcs_blob,
        "size_bytes":  len(file_bytes),
    }
