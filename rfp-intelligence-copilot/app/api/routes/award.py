"""
award.py  — Award Agent routes  (v2: push_log instrumentation)
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db import get_db
from app.api.routes.agent_logs import push_log

router = APIRouter(tags=["Award"])

try:
    from app.agents.award_agent import AwardAgent
    _AWARD_AGENT_AVAILABLE = True
except ImportError:
    _AWARD_AGENT_AVAILABLE = False


class AwardRecommendRequest(BaseModel):
    project_id: str
    suppliers: List[Dict[str, Any]] = Field(
        ..., description="List of scored suppliers from technical analysis"
    )
    scoring_weights: Optional[Dict[str, float]] = None
    budget_cap: Optional[float] = None
    notes: Optional[str] = ""


class AwardScoreRequest(BaseModel):
    project_id: str
    supplier_id: str
    technical_score: float
    price_score: float
    compliance_score: float
    weights: Optional[Dict[str, float]] = None


@router.post("/recommend")
async def award_recommendation(payload: AwardRecommendRequest):
    """
    Run the Award Agent to score and rank suppliers, producing a final recommendation.
    """
    push_log(agent_id="award", status="running",
             message=f"Scoring {len(payload.suppliers)} supplier(s) for award recommendation")
    if not _AWARD_AGENT_AVAILABLE:
        push_log(agent_id="award", status="error",
                 message="AwardAgent not available — check backend dependencies")
        raise HTTPException(503, detail="AwardAgent not available")

    try:
        t0    = time.time()
        agent = AwardAgent()
        result = agent.run({
            "project_id":      payload.project_id,
            "suppliers":       payload.suppliers,
            "scoring_weights": payload.scoring_weights or {},
            "budget_cap":      payload.budget_cap,
            "notes":           payload.notes or "",
        })
        winner = result.get("recommended_supplier", "N/A")
        push_log(agent_id="award", status="complete",
                 message=f"Award recommendation: {winner}",
                 confidence=result.get("confidence", 85),
                 duration_ms=int((time.time() - t0) * 1000))
        return result
    except Exception as e:
        push_log(agent_id="award", status="error",
                 message=f"Award scoring failed: {e}")
        raise HTTPException(500, detail=str(e))


@router.post("/score")
async def score_supplier(payload: AwardScoreRequest):
    """
    Compute composite award score for a single supplier.
    """
    push_log(agent_id="award", status="running",
             message=f"Computing composite score for supplier {payload.supplier_id}")
    weights = payload.weights or {
        "technical": 0.4,
        "price": 0.35,
        "compliance": 0.25,
    }
    composite = round(
        payload.technical_score  * weights.get("technical",   0.4)
        + payload.price_score    * weights.get("price",       0.35)
        + payload.compliance_score * weights.get("compliance", 0.25),
        2,
    )
    push_log(agent_id="award", status="complete",
             message=f"Supplier {payload.supplier_id} scored {composite:.1f}/10",
             confidence=85)
    return {
        "project_id":       payload.project_id,
        "supplier_id":      payload.supplier_id,
        "composite_score":  composite,
        "component_scores": {
            "technical":   payload.technical_score,
            "price":       payload.price_score,
            "compliance":  payload.compliance_score,
        },
        "weights_used": weights,
    }


@router.get("/status/{project_id}")
async def award_status(project_id: str):
    """
    Return award workflow status for a project.
    """
    return {
        "project_id": project_id,
        "status":     "ready",
        "message":    "Award Agent ready. Submit suppliers via POST /award/recommend.",
    }
