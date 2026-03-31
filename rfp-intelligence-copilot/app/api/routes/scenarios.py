from fastapi import APIRouter, HTTPException
from app.models.schemas import ScenarioRequest, ScenarioResponse
from app.services.scenario_engine import run_custom_scenario

router = APIRouter()


@router.post("/run", response_model=ScenarioResponse)
def scenario_run(req: ScenarioRequest):
    # Legacy fixed-data stub kept for backward-compat while pricing cost_model
    # is not yet plumbed into this endpoint.
    base = [
        {"supplier_id": "sup_1", "items": [{"question_id": "q1", "score": 8, "weight": 2}], "compliance_score": 1},
        {"supplier_id": "sup_2", "items": [{"question_id": "q1", "score": 7, "weight": 2}], "compliance_score": 1},
    ]
    try:
        # Build a minimal cost_model stub so the engine doesn't crash
        cost_model = {
            "suppliers": [s["supplier_id"] for s in base],
            "matrix": {},
            "category_matrix": {},
        }
        result = run_custom_scenario(
            user_input=str(req.weight_adjustments),
            cost_model=cost_model,
        )
        ranking = [
            {
                "supplier_id": sup,
                "score": round(val, 2),
                "rank": i + 1,
            }
            for i, (sup, val) in enumerate(
                sorted(result["award_split"].items(), key=lambda x: -x[1])
            )
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return ScenarioResponse(scenario_id=result["scenario_id"], ranking=ranking)
