from fastapi import APIRouter
from app.models.schemas import ScenarioRequest, ScenarioResponse
from app.services.scenario_engine import run_scenario

router = APIRouter()

@router.post("/run", response_model=ScenarioResponse)
def scenario_run(req: ScenarioRequest):
    base = [
        {"supplier_id": "sup_1", "items": [{"question_id": "q1", "score": 8, "weight": 2}], "compliance_score": 1},
        {"supplier_id": "sup_2", "items": [{"question_id": "q1", "score": 7, "weight": 2}], "compliance_score": 1},
    ]
    ranking = run_scenario(base, req.weight_adjustments, req.excluded_suppliers, req.compliance_threshold)
    return ScenarioResponse(scenario_id="scenario_1", ranking=ranking)