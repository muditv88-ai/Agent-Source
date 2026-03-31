"""
schemas.py

All existing models are PRESERVED exactly as-is.
New models are appended at the bottom — fully backward compatible.
"""
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
from datetime import datetime

# ════════════════════════════════════════════════════════════════════════════
# EXISTING MODELS — DO NOT MODIFY
# ════════════════════════════════════════════════════════════════════════════

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


# ════════════════════════════════════════════════════════════════════════════
# NEW MODELS — v2.0 enhancements (all fields Optional for backward compat)
# ════════════════════════════════════════════════════════════════════════════

# ── Module state tracking ────────────────────────────────────────────────────
MODULE_STATE_VALUES = ["pending", "active", "complete", "error"]

class ModuleStates(BaseModel):
    """
    Tracks independent completion state of each module per project.
    All fields default to 'pending' so existing projects get valid states.
    """
    rfp_state:       str = Field(default="pending", description="pending|active|complete|error")
    technical_state: str = Field(default="pending", description="pending|active|complete|error")
    pricing_state:   str = Field(default="pending", description="pending|active|complete|error")

    def to_dict(self) -> dict:
        return self.dict()


# ── Extended project metadata ────────────────────────────────────────────────
class ProjectMeta(BaseModel):
    """
    Optional enrichment fields for a project.
    All fields nullable so existing project records remain valid.
    """
    category:     Optional[str]       = Field(None, description="e.g. IT, Logistics, Professional Services")
    description:  Optional[str]       = Field(None, description="Free-text project description")
    stakeholders: Optional[List[str]] = Field(None, description="List of stakeholder names/emails")
    timeline:     Optional[str]       = Field(None, description="e.g. Q2 2026, or ISO date string")
    budget:       Optional[float]     = Field(None, description="Indicative budget (numeric)")
    currency:     Optional[str]       = Field(None, description="ISO currency code e.g. USD, INR")


class ProjectMetaUpdateRequest(BaseModel):
    """Request body for PATCH /projects/{id}/meta"""
    category:     Optional[str]       = None
    description:  Optional[str]       = None
    stakeholders: Optional[List[str]] = None
    timeline:     Optional[str]       = None
    budget:       Optional[float]     = None
    currency:     Optional[str]       = None


class ModuleStateUpdateRequest(BaseModel):
    """Request body for PATCH /projects/{id}/module-states"""
    module: str   = Field(..., description="rfp | technical | pricing")
    state:  str   = Field(..., description="pending | active | complete | error")


# ── RFP structured view ──────────────────────────────────────────────────────
class SupplierInfoField(BaseModel):
    field_name:  str
    value:       Optional[str] = None
    editable:    bool          = True

class TechnicalQuestionField(BaseModel):
    question_id:      str
    question_text:    str
    category:         str
    weight:           float
    question_type:    str
    scoring_guidance: Optional[str] = None

class PricingField(BaseModel):
    field_name:      str
    field_type:      str   = Field(default="supplier_filled",
                                   description="buyer_defined | supplier_filled")
    sku:             Optional[str]   = None
    description:     Optional[str]  = None
    quantity:        Optional[float] = None
    unit:            Optional[str]  = None
    category:        Optional[str]  = None

class RFPStructuredView(BaseModel):
    """
    Structured decomposition of an RFP into three sections.
    Returned by GET /rfp/{rfp_id}/structured-view
    """
    project_id:           str
    rfp_filename:         Optional[str]              = None
    supplier_info:        List[SupplierInfoField]     = []
    technical_questions:  List[TechnicalQuestionField] = []
    pricing_fields:       List[PricingField]          = []
    structure_type:       Optional[str]               = None   # from pricing_parser
    currency:             Optional[str]               = None
    parse_warnings:       List[str]                   = []
    cached:               bool                        = False


# ── Feature flags ────────────────────────────────────────────────────────────
class FeatureFlags(BaseModel):
    """
    Per-project feature flags. Safe defaults ensure no disruption to
    existing projects that don't have a feature_flags.json yet.
    """
    chatbot_actions:       bool = Field(default=True,  description="Allow chatbot to trigger mutations")
    new_analysis_engine:   bool = Field(default=False, description="Use v2 analysis engine (Phase 2)")
    pricing_scenarios:     bool = Field(default=True,  description="Enable award scenario builder")
    structured_rfp_view:   bool = Field(default=False, description="Enable structured RFP view")
    audit_logging:         bool = Field(default=True,  description="Log chatbot actions to audit trail")


class FeatureFlagUpdateRequest(BaseModel):
    """Request body for PATCH /projects/{id}/feature-flags"""
    chatbot_actions:     Optional[bool] = None
    new_analysis_engine: Optional[bool] = None
    pricing_scenarios:   Optional[bool] = None
    structured_rfp_view: Optional[bool] = None
    audit_logging:       Optional[bool] = None


# ── Audit log ────────────────────────────────────────────────────────────────
class AuditLogEntry(BaseModel):
    """
    Immutable record of a chatbot or API mutation.
    Written by audit_logger.py; read by GET /projects/{id}/audit-log
    """
    entry_id:   str
    project_id: str
    actor:      str                  = "system"   # user email or "system" or "chatbot"
    action:     str                               # e.g. "pricing_scenario", "rescore", "weight_adjust"
    module:     str                  = ""         # rfp | technical | pricing | chat
    payload:    Dict[str, Any]       = {}
    reversible: bool                 = False
    timestamp:  str                               # ISO 8601


# ── Chat context (typed) ─────────────────────────────────────────────────────
class ChatContext(BaseModel):
    """
    Typed context bag passed to POST /chat.
    All fields optional — the endpoint stays backward compatible
    with callers that don't populate these fields.
    """
    project_id:         Optional[str]        = None
    rfp_id:             Optional[str]        = None
    suppliers:          Optional[List[str]]  = None
    # Technical analysis context
    category_scores:    Optional[Dict[str, Any]] = None
    award_recommendation: Optional[Dict[str, Any]] = None
    # Pricing context
    total_costs:        Optional[List[Dict[str, Any]]] = None
    cost_model:         Optional[Dict[str, Any]] = None
    category_matrix:    Optional[Dict[str, Any]] = None
    categories:         Optional[List[str]]  = None
    # Feature flags for this session
    feature_flags:      Optional[FeatureFlags] = None
