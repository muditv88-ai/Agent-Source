from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any

# ── Upload ───────────────────────────────────────────────────────────────────
class UploadResponse(BaseModel):
    rfp_id: str
    filename: str
    status: str

# ── Parse ────────────────────────────────────────────────────────────────────
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

# ── Supplier Upload ─────────────────────────────────────────────────────────────
class SupplierUploadResponse(BaseModel):
    rfp_id: str
    supplier_id: str
    supplier_name: str
    status: str

# ── Scoring config per project ───────────────────────────────────────────────────
class ScoringConfig(BaseModel):
    """
    Per-project weighting between Technical and Commercial scores.
    tech_weight + commercial_weight must equal 100.
    """
    tech_weight: float       = Field(default=70.0, ge=0, le=100)
    commercial_weight: float = Field(default=30.0, ge=0, le=100)
    dual_llm: bool           = Field(default=True,  description="Use second LLM to cross-check scores")

# ── Analysis ───────────────────────────────────────────────────────────────────
class QuestionScore(BaseModel):
    question_id: str
    question_text: str
    category: str
    question_type: str
    weight: float
    score: float                    # 0-10 (final, after dual-LLM reconciliation)
    primary_score: float  = 0.0
    checker_score: float  = 0.0
    score_delta: float    = 0.0     # |primary - checker|; high = flag
    flagged: bool         = False   # True when delta > threshold
    rationale: str        = ""
    checker_rationale: str = ""
    supplier_answer: str  = ""

class CategoryScore(BaseModel):
    category: str
    weighted_score: float
    question_count: int
    questions: List[QuestionScore]
    is_commercial: bool = False     # True for Commercial/Pricing category

class PriceComparison(BaseModel):
    """Row-level price comparison extracted from Commercial section."""
    line_item: str
    rfp_value:  Optional[str] = None  # value from RFP template
    suppliers:  Dict[str, str] = {}   # {supplier_name: value}
    unit: str = ""

class SupplierResult(BaseModel):
    supplier_id: str
    supplier_name: str
    overall_score: float
    technical_score: float  = 0.0
    commercial_score: float = 0.0
    rank: int
    category_scores: List[CategoryScore]
    strengths: List[str]
    weaknesses: List[str]
    recommendation: str
    flagged_questions: int  = 0     # number of questions flagged by dual-LLM

class AnalysisResponse(BaseModel):
    rfp_id: str
    status: str
    suppliers: List[SupplierResult]
    top_recommendation: str
    analysis_summary: str
    price_comparison: List[PriceComparison] = []
    scoring_config: Optional[ScoringConfig] = None

# ── Scenario ─────────────────────────────────────────────────────────────────────
class ScenarioRequest(BaseModel):
    rfp_id: str
    weight_adjustments: Dict[str, float] = {}
    excluded_suppliers: List[str] = []
    compliance_threshold: Optional[float] = None
    tech_weight: float = 70.0
    commercial_weight: float = 30.0

class ScenarioResponse(BaseModel):
    scenario_id: str
    ranking: List[Dict[str, Any]]

# ── Communications ────────────────────────────────────────────────────────────
class ClarificationRequest(BaseModel):
    supplier_id: str
    questions: List[str]

class ClarificationResponse(BaseModel):
    subject: str
    body: str

# ── Analysis Request ────────────────────────────────────────────────────────────
class AnalysisRequest(BaseModel):
    rfp_id: str
    tech_weight: float       = 70.0
    commercial_weight: float = 30.0
    dual_llm: bool           = True
