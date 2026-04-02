"""
scenarios.py  — Deadline Agent / Scenario planning routes  (v2: push_log instrumentation)
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db import get_db
from app.api.routes.agent_logs import push_log

router = APIRouter(tags=["Scenarios"])

try:
    from app.agents.deadline_agent import DeadlineAgent
    _DEADLINE_AGENT_AVAILABLE = True
except ImportError:
    _DEADLINE_AGENT_AVAILABLE = False

try:
    from app.models.scenario import Scenario
    _SCENARIO_MODEL_AVAILABLE = True
except ImportError:
    _SCENARIO_MODEL_AVAILABLE = False


class ScenarioCreateRequest(BaseModel):
    project_id: str
    title: str
    description: Optional[str] = ""
    milestones: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    constraints: Optional[Dict[str, Any]] = Field(default_factory=dict)


class DeadlineAnalysisRequest(BaseModel):
    project_id: str
    milestones: List[Dict[str, Any]] = Field(
        ..., description="[{name, due_date, dependencies, status}]"
    )
    context: Optional[str] = ""


class RiskRequest(BaseModel):
    project_id: str
    scenario_id: Optional[str] = None
    factors: Optional[List[str]] = Field(default_factory=list)


@router.post("/analyze-deadline")
async def analyze_deadline(payload: DeadlineAnalysisRequest):
    """
    Run the Deadline Agent to identify timeline risks and suggest mitigations.
    """
    push_log(agent_id="deadline", status="running",
             message=f"Analysing {len(payload.milestones)} milestones for timeline risk")
    if not _DEADLINE_AGENT_AVAILABLE:
        push_log(agent_id="deadline", status="error",
                 message="DeadlineAgent not available")
        raise HTTPException(503, detail="DeadlineAgent not available")

    try:
        t0    = time.time()
        agent = DeadlineAgent()
        result = agent.run({
            "project_id": payload.project_id,
            "milestones": payload.milestones,
            "context":    payload.context or "",
        })
        risk_count = len(result.get("risks", []))
        push_log(agent_id="deadline", status="complete",
                 message=f"Timeline analysis complete — {risk_count} risk(s) identified",
                 duration_ms=int((time.time() - t0) * 1000))
        return result
    except Exception as e:
        push_log(agent_id="deadline", status="error",
                 message=f"Deadline analysis failed: {e}")
        raise HTTPException(500, detail=str(e))


@router.post("/create")
async def create_scenario(
    payload: ScenarioCreateRequest,
    db: Session = Depends(get_db),
):
    """
    Create a procurement scenario (timeline + constraints snapshot).
    """
    push_log(agent_id="deadline", status="running",
             message=f"Creating scenario: {payload.title}")

    if _SCENARIO_MODEL_AVAILABLE:
        try:
            scenario = Scenario(
                project_id=payload.project_id,
                title=payload.title,
                description=payload.description or "",
            )
            db.add(scenario)
            db.commit()
            db.refresh(scenario)
            push_log(agent_id="deadline", status="complete",
                     message=f"Scenario '{payload.title}' created")
            return {"scenario_id": scenario.id, "title": payload.title, "status": "created"}
        except Exception as e:
            push_log(agent_id="deadline", status="error", message=str(e))
            raise HTTPException(500, detail=str(e))
    else:
        push_log(agent_id="deadline", status="complete",
                 message=f"Scenario '{payload.title}' queued (in-memory)")
        return {
            "scenario_id": f"temp_{payload.project_id}_{int(time.time())}",
            "title":       payload.title,
            "status":      "queued",
            "note":        "Scenario model not yet migrated — stored in memory only",
        }


@router.post("/risk-assessment")
async def risk_assessment(payload: RiskRequest):
    """
    Run a risk assessment for a scenario or project.
    """
    push_log(agent_id="deadline", status="running",
             message=f"Running risk assessment for project {payload.project_id}")
    if not _DEADLINE_AGENT_AVAILABLE:
        push_log(agent_id="deadline", status="error",
                 message="DeadlineAgent not available")
        raise HTTPException(503, detail="DeadlineAgent not available")

    try:
        t0    = time.time()
        agent = DeadlineAgent()
        result = agent.assess_risk({
            "project_id":  payload.project_id,
            "scenario_id": payload.scenario_id,
            "factors":     payload.factors or [],
        })
        push_log(agent_id="deadline", status="complete",
                 message=f"Risk assessment complete for project {payload.project_id}",
                 duration_ms=int((time.time() - t0) * 1000))
        return result
    except Exception as e:
        push_log(agent_id="deadline", status="error", message=str(e))
        raise HTTPException(500, detail=str(e))


@router.get("/list/{project_id}")
async def list_scenarios(
    project_id: str,
    db: Session = Depends(get_db),
):
    """
    Return all scenarios for a project.
    """
    if _SCENARIO_MODEL_AVAILABLE:
        try:
            from sqlmodel import select
            scenarios = db.exec(
                select(Scenario).where(Scenario.project_id == project_id)
            ).all()
            return {"scenarios": [s.model_dump() for s in scenarios], "total": len(scenarios)}
        except Exception as e:
            raise HTTPException(500, detail=str(e))
    return {"scenarios": [], "total": 0, "note": "Scenario model not yet migrated"}
