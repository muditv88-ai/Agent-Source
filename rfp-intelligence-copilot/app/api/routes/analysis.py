"""
analysis.py  —  Technical Analysis API routes

FM-6.1  Run AI scoring for all supplier responses
FM-6.2  Weight configurator  (per-session overrides)
FM-6.3  Gap analysis         (weak areas + disqualification)
FM-6.4  Narrative report     (per-supplier summary)
FM-6.5  Disqualification     (auto-flag suppliers below threshold)

All heavy lifting is delegated to TechnicalAnalysisAgent which
wraps ai_scorer.py cleanly — that service file is NOT modified.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents.technical_analysis_agent import TechnicalAnalysisAgent

router = APIRouter(prefix="/analysis", tags=["Technical Analysis"])


# ── Request / Response models ─────────────────────────────────────────────────

class RunAnalysisRequest(BaseModel):
    project_id: str
    questions: List[Dict[str, Any]] = Field(
        ..., description="RFP questions with question_id, text, weight, category"
    )
    supplier_responses: Dict[str, Dict[str, str]] = Field(
        ..., description="{supplier_name: {question_id: answer_text}}"
    )
    weight_overrides: Optional[Dict[str, float]] = Field(
        default=None,
        description="Override weights per category, e.g. {\"quality\": 0.4}"
    )
    min_score: float = Field(default=4.0, description="Gap threshold (0-10)")
    disqualify_threshold: float = Field(
        default=2.0, description="Score below which a question is critically weak"
    )
    disqualify_max_weak: int = Field(
        default=2, description="Max critically-weak questions before disqualification"
    )


class GapAnalysisRequest(BaseModel):
    project_id: str
    supplier_scores: Dict[str, Dict[str, Any]] = Field(
        ..., description="Output of /analysis/run — {supplier: {qid: {score, rationale}}}"
    )
    questions: List[Dict[str, Any]]
    min_score: float = 4.0
    disqualify_threshold: float = 2.0
    disqualify_max_weak: int = 2


class ReportRequest(BaseModel):
    project_id: str
    supplier_name: str
    category_scores: List[Dict[str, Any]] = Field(
        default=[], description="[{category, score, weight}]"
    )
    overall_score: float


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/run")
async def run_analysis(payload: RunAnalysisRequest):
    """
    FM-6.1 / FM-6.2 — Score all suppliers with optional weight overrides.
    Returns per-supplier {question_id: {score, rationale}}, gap analysis,
    narrative reports, and a disqualified list.
    """
    try:
        agent = TechnicalAnalysisAgent(
            weights=payload.weight_overrides or {},
            min_score=payload.min_score,
            disqualify_threshold=payload.disqualify_threshold,
            disqualify_max_weak=payload.disqualify_max_weak,
        )
        result = agent.run({
            "questions": payload.questions,
            "supplier_responses": payload.supplier_responses,
        })
        result["project_id"] = payload.project_id
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gap")
async def gap_analysis(payload: GapAnalysisRequest):
    """
    FM-6.3 — Run gap analysis on pre-computed scores.
    Useful when scores already exist and only the gap view needs refreshing.
    """
    try:
        agent = TechnicalAnalysisAgent(
            min_score=payload.min_score,
            disqualify_threshold=payload.disqualify_threshold,
            disqualify_max_weak=payload.disqualify_max_weak,
        )
        gaps = agent._gap_analysis(
            supplier_scores=payload.supplier_scores,
            questions=payload.questions,
        )
        disqualified = [s for s, g in gaps.items() if g.get("disqualified")]
        return {
            "project_id": payload.project_id,
            "gaps": gaps,
            "disqualified": disqualified,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/report")
async def generate_report(payload: ReportRequest):
    """
    FM-6.4 — Generate a narrative evaluation report for a single supplier.
    Calls generate_supplier_summary from ai_scorer.py via the agent.
    """
    try:
        agent = TechnicalAnalysisAgent()
        report = agent._generate_report(
            supplier_name=payload.supplier_name,
            category_scores=payload.category_scores,
            overall_score=payload.overall_score,
        )
        return {"project_id": payload.project_id, "report": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/weights/defaults")
async def get_default_weights():
    """
    FM-6.2 — Return the default weight categories so the UI can render sliders.
    Extend this list to match the actual RFP question categories in your data.
    """
    return {
        "categories": [
            {"key": "technical", "label": "Technical Capability", "default_weight": 0.35},
            {"key": "quality",   "label": "Quality & Compliance",  "default_weight": 0.25},
            {"key": "delivery",  "label": "Delivery & Lead Time",   "default_weight": 0.20},
            {"key": "commercial","label": "Commercial",             "default_weight": 0.20},
        ]
    }
