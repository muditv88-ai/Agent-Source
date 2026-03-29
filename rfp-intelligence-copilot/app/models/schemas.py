from pydantic import BaseModel
from typing import Dict, List, Optional, Any

class UploadResponse(BaseModel):
    rfp_id: str
    filename: str
    status: str

class ParseResponse(BaseModel):
    rfp_id: str
    status: str
    canonical_model: Dict[str, Any]

class AnalysisRequest(BaseModel):
    rfp_id: str

class AnalysisResponse(BaseModel):
    rfp_id: str
    ranked_suppliers: List[Dict[str, Any]]

class ScenarioRequest(BaseModel):
    rfp_id: str
    weight_adjustments: Dict[str, float] = {}
    excluded_suppliers: List[str] = []
    compliance_threshold: Optional[float] = None

class ScenarioResponse(BaseModel):
    scenario_id: str
    ranking: List[Dict[str, Any]]

class ClarificationRequest(BaseModel):
    supplier_id: str
    questions: List[str]

class ClarificationResponse(BaseModel):
    subject: str
    body: str