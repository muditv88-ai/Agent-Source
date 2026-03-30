from pydantic import BaseModel
from typing import Dict, List, Optional, Any

# ── Upload ──────────────────────────────────────────────────────────────────
class UploadResponse(BaseModel):
    rfp_id: str
    filename: str
    status: str

# ── Parse ───────────────────────────────────────────────────────────────────
class RFPQuestion(BaseModel):
    question_id: str
    category: str
    question_text: str
    question_type: str          # "quantitative" | "qualitative"
    weight: float               # 0-100
    scoring_guidance: Optional[str] = None

class ParseResponse(BaseModel):
    rfp_id: str
    status: str
    questions: List[RFPQuestion]
    categories: List[str]
    total_questions: int

# ── Supplier Upload ──────────────────────────────────────────────────────────
class SupplierUploadResponse(BaseModel):
    rfp_id: str
    supplier_id: str
    supplier_name: str
    status: str

# ── Analysis ────────────────────────────────────────────────────────────────
class QuestionScore(BaseModel):
    question_id: str
    question_text: str
    category: str
    question_type: str
    weight: float
    score: float                # 0-10
    rationale: str
    supplier_answer: str

class CategoryScore(BaseModel):
    category: str
    weighted_score: float       # 0-10
    question_count: int
    questions: List[QuestionScore]

class SupplierResult(BaseModel):
    supplier_id: str
    supplier_name: str
    overall_score: float        # 0-10
    rank: int
    category_scores: List[CategoryScore]
    strengths: List[str]
    weaknesses: List[str]
    recommendation: str

class AnalysisResponse(BaseModel):
    rfp_id: str
    status: str
    suppliers: List[SupplierResult]
    top_recommendation: str
    analysis_summary: str

# ── Scenario ─────────────────────────────────────────────────────────────────
class ScenarioRequest(BaseModel):
    rfp_id: str
    weight_adjustments: Dict[str, float] = {}
    excluded_suppliers: List[str] = []
    compliance_threshold: Optional[float] = None

class ScenarioResponse(BaseModel):
    scenario_id: str
    ranking: List[Dict[str, Any]]

# ── Communications ───────────────────────────────────────────────────────────
class ClarificationRequest(BaseModel):
    supplier_id: str
    questions: List[str]

class ClarificationResponse(BaseModel):
    subject: str
    body: str

# ── Analysis Request ─────────────────────────────────────────────────────────
class AnalysisRequest(BaseModel):
    rfp_id: str
