"""
scenarios.py  v2.0

Fully-implemented scenario endpoints. Replaces the fixed-data stub.
All scenarios are project-scoped and use the real pricing cost_model
already computed by /pricing-analysis.

Endpoints
---------
POST   /scenarios/{project_id}/run
    Parse natural-language scenario + run against real pricing data.
    Saves result to scenarios.json.

GET    /scenarios/{project_id}
    List all saved scenarios for a project.

GET    /scenarios/{project_id}/{scenario_id}
    Retrieve a single saved scenario.

DELETE /scenarios/{project_id}/{scenario_id}
    Delete a saved scenario.

POST   /scenarios/{project_id}/compare
    Compare two or more saved scenarios side-by-side.

GET    /scenarios/{project_id}/presets
    Return a menu of common scenario prompts to seed the UI.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.scenario_engine import run_custom_scenario
from app.services.project_store import (
    get_project, load_metadata, save_metadata
)
from app.services.pricing_store import load_cost_model

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Request / response models
# ─────────────────────────────────────────────────────────────────────────────

class ScenarioRunRequest(BaseModel):
    user_input: str = Field(
        ...,
        description="Natural-language scenario description. "
                    "e.g. 'Award 60% to Supplier A, rest to cheapest' or "
                    "'Exclude Supplier C'",
        min_length=3,
    )
    scenario_id: Optional[str] = Field(
        None,
        description="Optional custom ID. Auto-generated if omitted."
    )


class ScenarioCompareRequest(BaseModel):
    scenario_ids: List[str] = Field(
        ...,
        description="List of 2-5 saved scenario IDs to compare.",
        min_items=2,
        max_items=5,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _require_project(project_id: str):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _load_scenarios(project_id: str) -> dict:
    return load_metadata(project_id, "scenarios.json") or {}


def _save_scenario(project_id: str, result: dict):
    saved = _load_scenarios(project_id)
    saved[result["scenario_id"]] = result
    save_metadata(project_id, "scenarios.json", saved)


def _require_cost_model(project_id: str) -> dict:
    cost_model = load_cost_model(project_id)
    if not cost_model:
        raise HTTPException(
            status_code=400,
            detail="No pricing analysis found for this project. "
                   "Run /pricing-analysis/{project_id} first — "
                   "scenarios need real cost data to execute.",
        )
    return cost_model


# ─────────────────────────────────────────────────────────────────────────────
# POST /scenarios/{project_id}/run
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/run", status_code=201)
def run_scenario(project_id: str, body: ScenarioRunRequest):
    """
    Parse a natural-language award scenario and execute it against the
    project's real pricing cost_model.

    Returns full scenario result:
      - scenario_id, user_input, intent (parsed rules)
      - granularity (sku | category)
      - total_cost, award_split {supplier: value}
      - breakdown (SKU-level) or allocation (category-level)
      - active_suppliers
    """
    _require_project(project_id)
    cost_model = _require_cost_model(project_id)

    try:
        result = run_custom_scenario(
            user_input=body.user_input,
            cost_model=cost_model,
            scenario_id=body.scenario_id,
        )
    except Exception as exc:
        logger.exception("Scenario engine failed for project %s", project_id)
        raise HTTPException(status_code=500, detail=str(exc))

    result["project_id"] = project_id
    _save_scenario(project_id, result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# GET /scenarios/{project_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{project_id}")
def list_scenarios(project_id: str):
    """List all saved scenarios for a project, sorted by scenario_id desc."""
    _require_project(project_id)
    saved = _load_scenarios(project_id)
    items = sorted(saved.values(), key=lambda s: s.get("scenario_id", ""), reverse=True)
    return {"project_id": project_id, "count": len(items), "scenarios": items}


# ─────────────────────────────────────────────────────────────────────────────
# GET /scenarios/{project_id}/{scenario_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{project_id}/{scenario_id}")
def get_scenario(project_id: str, scenario_id: str):
    """Retrieve a single saved scenario."""
    _require_project(project_id)
    saved = _load_scenarios(project_id)
    scenario = saved.get(scenario_id)
    if not scenario:
        raise HTTPException(
            status_code=404,
            detail=f"No scenario with id '{scenario_id}' found for this project."
        )
    return scenario


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /scenarios/{project_id}/{scenario_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/{project_id}/{scenario_id}")
def delete_scenario(project_id: str, scenario_id: str):
    """Delete a saved scenario."""
    _require_project(project_id)
    saved = _load_scenarios(project_id)
    if scenario_id not in saved:
        raise HTTPException(
            status_code=404,
            detail=f"No scenario with id '{scenario_id}' found for this project."
        )
    del saved[scenario_id]
    save_metadata(project_id, "scenarios.json", saved)
    return {"project_id": project_id, "scenario_id": scenario_id, "deleted": True}


# ─────────────────────────────────────────────────────────────────────────────
# POST /scenarios/{project_id}/compare
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/compare")
def compare_scenarios(project_id: str, body: ScenarioCompareRequest):
    """
    Compare 2-5 saved scenarios side-by-side.

    Returns a comparison table:
    {
      "scenarios": [{scenario_id, user_input, total_cost, award_split, granularity}],
      "cheapest_scenario_id": "...",
      "savings_vs_worst": 1234.56
    }
    """
    _require_project(project_id)
    saved = _load_scenarios(project_id)

    results = []
    missing = []
    for sid in body.scenario_ids:
        if sid in saved:
            results.append(saved[sid])
        else:
            missing.append(sid)

    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Scenario(s) not found: {missing}. Run them first via POST /scenarios/{{project_id}}/run."
        )

    # Build compact comparison rows
    rows = [
        {
            "scenario_id":   s["scenario_id"],
            "user_input":    s.get("user_input", ""),
            "granularity":   s.get("granularity", "sku"),
            "total_cost":    s.get("total_cost", 0),
            "award_split":   s.get("award_split", {}),
            "active_suppliers": s.get("active_suppliers", []),
        }
        for s in results
    ]

    costs = [r["total_cost"] for r in rows if r["total_cost"]]
    cheapest_id = rows[costs.index(min(costs))]["scenario_id"] if costs else None
    savings = round(max(costs) - min(costs), 2) if len(costs) >= 2 else 0.0

    return {
        "project_id":           project_id,
        "compared_count":       len(rows),
        "scenarios":            rows,
        "cheapest_scenario_id": cheapest_id,
        "savings_vs_worst":     savings,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /scenarios/{project_id}/presets
# ─────────────────────────────────────────────────────────────────────────────

_PRESET_SCENARIOS = [
    {
        "id": "cheapest_overall",
        "label": "Cheapest Overall",
        "prompt": "Award all items to the cheapest supplier for each line item",
        "description": "Pure cost optimisation — award every SKU to whoever is cheapest.",
    },
    {
        "id": "single_source",
        "label": "Single-Source",
        "prompt": "Award everything to the single cheapest overall supplier",
        "description": "Consolidate spend with one supplier for simplicity and volume discount potential.",
    },
    {
        "id": "dual_source",
        "label": "Dual-Source (Risk Split)",
        "prompt": "Split items evenly between the two cheapest suppliers",
        "description": "Reduce supply risk by splitting the award across two suppliers.",
    },
    {
        "id": "exclude_highest",
        "label": "Exclude Most Expensive",
        "prompt": "Exclude the most expensive supplier and award to the next cheapest",
        "description": "Drop the highest-cost bidder and re-optimise across the rest.",
    },
    {
        "id": "category_split",
        "label": "Category-Based Split",
        "prompt": "Award each category to the cheapest supplier in that category",
        "description": "Optimise at category level — each supplier wins the categories where they are strongest.",
    },
    {
        "id": "custom",
        "label": "Custom Scenario",
        "prompt": "",
        "description": "Describe your own award strategy in plain English.",
    },
]


@router.get("/{project_id}/presets")
def get_scenario_presets(project_id: str):
    """
    Return preset scenario prompts to seed the ScenariosPage UI.
    Project existence is validated but no project data is needed.
    """
    _require_project(project_id)
    return {
        "project_id": project_id,
        "presets": _PRESET_SCENARIOS,
    }
