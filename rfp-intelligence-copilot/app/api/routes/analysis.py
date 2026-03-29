from fastapi import APIRouter
from app.models.schemas import AnalysisRequest, AnalysisResponse
from app.services.scoring_engine import compute_weighted_score

router = APIRouter()

@router.post("/run", response_model=AnalysisResponse)
def run_analysis(req: AnalysisRequest):
    """Run supplier analysis and return ranked suppliers"""
    # Mock data for now - replace with real parsing later
    suppliers = [
        {
            "supplier_id": "supplier_a",
            "items": [{"score": 8.5, "weight": 2}, {"score": 9.0, "weight": 1}],
            "total_score": compute_weighted_score([{"score": 8.5, "weight": 2}, {"score": 9.0, "weight": 1}]),
            "compliance": True
        },
        {
            "supplier_id": "supplier_b", 
            "items": [{"score": 7.8, "weight": 2}, {"score": 8.2, "weight": 1}],
            "total_score": compute_weighted_score([{"score": 7.8, "weight": 2}, {"score": 8.2, "weight": 1}]),
            "compliance": True
        }
    ]
    
    return AnalysisResponse(
        rfp_id=req.rfp_id,
        ranked_suppliers=suppliers
    )