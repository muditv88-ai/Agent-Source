"""
award.py

HTTP endpoints that wire AwardAgent into the API surface.

Endpoints
---------
POST   /award/{project_id}/run
    Execute an award scenario (run_scenario + generate_narrative).
    Returns full scenario result including narrative memo.

GET    /award/{project_id}/saved
    List all saved award results for a project.

GET    /award/{project_id}/saved/{scenario_id}
    Retrieve a single saved award result.

DELETE /award/{project_id}/saved/{scenario_id}
    Delete a saved award result.

POST   /award/{project_id}/saved/{scenario_id}/approve
    Submit a saved scenario for manager approval (sends draft email).

POST   /award/{project_id}/saved/{scenario_id}/notify
    Send award/regret notifications to all suppliers (draft mode).
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents.award_agent import AwardAgent
from app.services.project_store import (
    get_project, load_metadata, save_metadata
)
from app.services.pricing_store import load_cost_model
from app.models.schemas import AwardRequest, AwardResult

logger = logging.getLogger(__name__)
router = APIRouter()

_agent = AwardAgent()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _require_project(project_id: str):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _load_saved(project_id: str) -> dict:
    """Load the award_results.json metadata file for a project."""
    return load_metadata(project_id, "award_results.json") or {}


def _save_result(project_id: str, result: dict):
    """Persist a single award result into award_results.json."""
    saved = _load_saved(project_id)
    saved[result["scenario_id"]] = result
    save_metadata(project_id, "award_results.json", saved)


# ─────────────────────────────────────────────────────────────────────────────
# POST /award/{project_id}/run
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/run", response_model=AwardResult, status_code=201)
def run_award(
    project_id: str,
    body: AwardRequest,
):
    """
    Execute an award scenario for a project.

    - Loads the pricing cost_model already computed by /pricing-analysis.
    - Passes it to AwardAgent.run() which runs the scenario engine +
      generates an LLM narrative memo.
    - Saves the result to award_results.json and returns it.
    """
    _require_project(project_id)

    # Pull cost_model from pricing analysis results
    cost_model = load_cost_model(project_id)
    if not cost_model:
        raise HTTPException(
            status_code=400,
            detail="No pricing analysis found for this project. "
                   "Run /pricing-analysis/{project_id} first.",
        )

    # Pull tech scores if analysis results exist
    analysis = load_metadata(project_id, "analysis_result.json") or {}
    tech_scores = {
        s["supplier_name"]: s.get("technical_score", 0)
        for s in analysis.get("suppliers", [])
    }

    try:
        result = _agent.run(
            input={
                "user_input": body.user_input,
                "cost_model": cost_model,
                "tech_scores": tech_scores,
            }
        )
    except Exception as exc:
        logger.exception("AwardAgent.run failed")
        raise HTTPException(status_code=500, detail=str(exc))

    result["project_id"] = project_id
    _save_result(project_id, result)
    return AwardResult(**result)


# ─────────────────────────────────────────────────────────────────────────────
# GET /award/{project_id}/saved
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{project_id}/saved")
def list_saved_awards(project_id: str):
    """Return all saved award results for a project, sorted newest-first."""
    _require_project(project_id)
    saved = _load_saved(project_id)
    results = sorted(saved.values(), key=lambda r: r.get("scenario_id", ""), reverse=True)
    return {"project_id": project_id, "count": len(results), "results": results}


# ─────────────────────────────────────────────────────────────────────────────
# GET /award/{project_id}/saved/{scenario_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{project_id}/saved/{scenario_id}")
def get_saved_award(project_id: str, scenario_id: str):
    """Retrieve a single saved award result by scenario_id."""
    _require_project(project_id)
    saved = _load_saved(project_id)
    result = saved.get(scenario_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No saved award result with id '{scenario_id}'"
        )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /award/{project_id}/saved/{scenario_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/{project_id}/saved/{scenario_id}")
def delete_saved_award(project_id: str, scenario_id: str):
    """Delete a saved award result."""
    _require_project(project_id)
    saved = _load_saved(project_id)
    if scenario_id not in saved:
        raise HTTPException(
            status_code=404,
            detail=f"No saved award result with id '{scenario_id}'"
        )
    del saved[scenario_id]
    save_metadata(project_id, "award_results.json", saved)
    return {"project_id": project_id, "scenario_id": scenario_id, "deleted": True}


# ─────────────────────────────────────────────────────────────────────────────
# POST /award/{project_id}/saved/{scenario_id}/approve
# ─────────────────────────────────────────────────────────────────────────────

class ApprovalRequest(BaseModel):
    approver_email: str = Field(..., description="Email of the manager to send approval request to")


@router.post("/{project_id}/saved/{scenario_id}/approve")
def submit_for_approval(project_id: str, scenario_id: str, body: ApprovalRequest):
    """
    Submit a saved award scenario for manager approval.
    Sends a draft approval-request email via CommsAgent.
    Updates approval_status to 'pending_approval'.
    """
    _require_project(project_id)
    saved = _load_saved(project_id)
    result = saved.get(scenario_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No saved award result with id '{scenario_id}'"
        )

    try:
        approval_result = _agent._submit_approval(
            scenario_id=scenario_id,
            approver_email=body.approver_email,
            project_id=project_id,
        )
    except Exception as exc:
        logger.exception("Approval submission failed")
        raise HTTPException(status_code=500, detail=str(exc))

    result["approval_status"] = "pending_approval"
    result["approver_email"] = body.approver_email
    _save_result(project_id, result)
    return {
        "project_id": project_id,
        "scenario_id": scenario_id,
        "approval_status": "pending_approval",
        "approver_email": body.approver_email,
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /award/{project_id}/saved/{scenario_id}/notify
# ─────────────────────────────────────────────────────────────────────────────

class NotifyRequest(BaseModel):
    suppliers: List[dict] = Field(
        ...,
        description="List of supplier dicts: [{name, email}]. "
                    "Award/regret is determined automatically from scenario."
    )


@router.post("/{project_id}/saved/{scenario_id}/notify")
def notify_suppliers(project_id: str, scenario_id: str, body: NotifyRequest):
    """
    Send award and regret notifications to all suppliers.
    Drafts only — CommsAgent sets auto_send=False so a human reviews before sending.
    """
    _require_project(project_id)
    saved = _load_saved(project_id)
    scenario = saved.get(scenario_id)
    if not scenario:
        raise HTTPException(
            status_code=404,
            detail=f"No saved award result with id '{scenario_id}'"
        )

    try:
        notifications = _agent._notify_suppliers(
            scenario=scenario,
            all_suppliers=body.suppliers,
            project_id=project_id,
        )
    except Exception as exc:
        logger.exception("Supplier notification drafting failed")
        raise HTTPException(status_code=500, detail=str(exc))

    scenario["notifications_drafted"] = True
    _save_result(project_id, scenario)
    return {
        "project_id": project_id,
        "scenario_id": scenario_id,
        "notifications": notifications,
        "note": "All notifications are drafts — review in Communications before sending.",
    }
