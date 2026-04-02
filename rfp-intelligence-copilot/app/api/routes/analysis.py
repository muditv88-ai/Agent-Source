"""
analysis.py  —  Technical Analysis API routes

FM-6.1  Run AI scoring for all supplier responses
FM-6.2  Weight configurator  (per-session overrides)
FM-6.3  Gap analysis         (weak areas + disqualification)
FM-6.4  Narrative report     (per-supplier summary)
FM-6.5  Disqualification     (auto-flag suppliers below threshold)

Background job flow (used by POST /projects/{id}/analyze):
  1. projects.py calls _run_analysis_job(project_id, job_id) as BackgroundTask.
  2. _do_analysis_job reads questions.json + supplier docs from project_store.
  3. Parses supplier documents, maps question_id -> full_text for each supplier.
  4. Runs TechnicalAnalysisAgent and shapes output into AnalysisResult schema.
  5. Stores result in job_store; GET /status/{job_id} returns it to the frontend.
"""
import asyncio
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents.technical_analysis_agent import TechnicalAnalysisAgent
from app.services.job_store import job_store
from app.services.project_store import (
    ensure_suppliers_local,
    load_metadata,
    update_module_state,
)
from app.services.document_parser import parse_document

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analysis", tags=["Technical Analysis"])
_executor = ThreadPoolExecutor(max_workers=4)


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


# ── Background job helpers ─────────────────────────────────────────────────────

def _overall(supplier_scores: Dict) -> float:
    vals = [
        float(v["score"])
        for v in supplier_scores.values()
        if isinstance(v, dict) and "score" in v
    ]
    return round(sum(vals) / max(len(vals), 1), 2) if vals else 0.0


def _shape_analysis_result(
    project_id: str,
    scores: Dict[str, Dict],
    gaps: Dict[str, Dict],
    reports: Dict[str, Dict],
    questions: List[Dict],
    supplier_responses: Dict[str, Dict[str, str]],
    disqualified: List[str],
) -> Dict[str, Any]:
    """
    Convert raw agent output into the AnalysisResult schema expected by the frontend:
    { project_id, suppliers: [SupplierResult], disqualified, analysis_summary, top_recommendation }
    """
    cat_questions: Dict[str, List[Dict]] = {}
    for q in questions:
        cat_questions.setdefault(q.get("category", "General"), []).append(q)

    suppliers_out = []
    for rank, (supplier_name, supplier_scores) in enumerate(
        sorted(scores.items(), key=lambda x: -_overall(x[1])), start=1
    ):
        answers = supplier_responses.get(supplier_name, {})
        category_scores_out = []

        for cat, qs in cat_questions.items():
            cat_scores_vals = []
            qs_out = []
            for q in qs:
                qid = q["question_id"]
                s = supplier_scores.get(qid, {})
                score_val = float(s.get("score", 0)) if isinstance(s, dict) else 0.0
                cat_scores_vals.append(score_val)
                qs_out.append({
                    "question_id":     qid,
                    "question_text":   q.get("question_text", ""),
                    "question_type":   q.get("question_type", "qualitative"),
                    "weight":          q.get("weight", 10),
                    "score":           score_val,
                    "supplier_answer": answers.get(qid, "")[:500],
                    "rationale":       s.get("rationale", "") if isinstance(s, dict) else "",
                    "flagged":         s.get("flagged", False) if isinstance(s, dict) else False,
                })
            cat_avg = round(sum(cat_scores_vals) / max(len(cat_scores_vals), 1), 2)
            category_scores_out.append({
                "category":       cat,
                "weighted_score": cat_avg,
                "questions":      qs_out,
            })

        overall = round(_overall(supplier_scores), 2)
        report = reports.get(supplier_name, {})
        gap    = gaps.get(supplier_name, {})

        suppliers_out.append({
            "supplier_id":     supplier_name,
            "supplier_name":   supplier_name,
            "rank":            rank,
            "overall_score":   overall,
            "category_scores": category_scores_out,
            "strengths":       report.get("strengths", []),
            "weaknesses":      report.get("weaknesses", []),
            "recommendation":  report.get("recommendation", ""),
            "disqualified":    supplier_name in disqualified,
            "weak_count":      gap.get("weak_count", 0),
        })

    top = suppliers_out[0] if suppliers_out else {}
    summary = (
        f"Evaluated {len(suppliers_out)} supplier(s) across "
        f"{len(questions)} questions in {len(cat_questions)} categories."
    )
    top_rec = top.get("recommendation") or (
        f"{top.get('supplier_name', 'N/A')} is the recommended supplier "
        f"with an overall score of {top.get('overall_score', 0):.1f}/10."
    ) if top else "No suppliers evaluated."

    return {
        "project_id":         project_id,
        "suppliers":          suppliers_out,
        "disqualified":       disqualified,
        "analysis_summary":   summary,
        "top_recommendation": top_rec,
    }


def _do_analysis_job(project_id: str) -> Dict[str, Any]:
    """
    Synchronous heavy-lifting: read project files, parse supplier docs, run agent.
    Called via thread-pool so the event loop is never blocked.
    """
    # 1. Load questions
    raw_qs = load_metadata(project_id, "questions.json") or []
    if not raw_qs:
        raise ValueError(
            "No questions found for this project. "
            "Please parse the RFP first (Upload RFP → Parse)."
        )

    # 2. Ensure supplier files are local
    supplier_paths = ensure_suppliers_local(project_id)
    if not supplier_paths:
        raise ValueError(
            "No supplier files found. "
            "Please upload at least one supplier response."
        )

    # 3. Load supplier name map
    name_meta = load_metadata(project_id, "suppliers.json") or {}

    # 4. Parse supplier docs → {supplier_name: {question_id: full_text}}
    supplier_responses: Dict[str, Dict[str, str]] = {}
    for path in supplier_paths:
        path = Path(path)
        supplier_name = (
            name_meta.get(str(path))
            or name_meta.get(path.name)
            or path.stem
        )
        try:
            parsed    = parse_document(str(path))
            full_text = parsed.get("full_text", "")
        except Exception as e:
            logger.warning("Could not parse %s: %s", path, e)
            full_text = ""

        # Map every question_id → full document text.
        # The scorer uses the question text to locate the relevant answer section.
        supplier_responses[supplier_name] = {
            q["question_id"]: full_text for q in raw_qs
        }

    if not supplier_responses:
        raise ValueError("All supplier documents failed to parse.")

    # 5. Run TechnicalAnalysisAgent
    agent  = TechnicalAnalysisAgent()
    result = agent.run({
        "questions":          raw_qs,
        "supplier_responses": supplier_responses,
    })

    # 6. Shape into frontend AnalysisResult schema
    return _shape_analysis_result(
        project_id=project_id,
        scores=result["scores"],
        gaps=result["gaps"],
        reports=result["reports"],
        questions=raw_qs,
        supplier_responses=supplier_responses,
        disqualified=result["disqualified"],
    )


async def _run_analysis_job(project_id: str, job_id: str, _rfp_id: str = ""):
    """
    Async wrapper invoked by projects.py as a FastAPI BackgroundTask.
    Signature matches: _analysis_module._run_analysis_job(project_id, job_id, project_id)
    """
    job_store.set_running(job_id)
    update_module_state(project_id, "technical", "active")
    try:
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(_executor, _do_analysis_job, project_id)
        job_store.set_completed(job_id, result)
        update_module_state(project_id, "technical", "complete")
    except Exception as e:
        err = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error("Analysis job %s failed: %s", job_id, err)
        job_store.set_failed(job_id, str(e))
        update_module_state(project_id, "technical", "error")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status/{job_id}")
async def get_analysis_status(job_id: str):
    """
    Polled by the frontend after POST /projects/{id}/analyze.
    Returns job status + result (AnalysisResult) when complete.
    """
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job_id,
        "status": job["status"],
        "result": job.get("result"),
        "error":  job.get("error"),
    }


@router.post("/run")
async def run_analysis(payload: RunAnalysisRequest):
    """
    FM-6.1 / FM-6.2 — Score all suppliers with optional weight overrides.
    Synchronous version for direct API callers (not via project background flow).
    """
    try:
        agent = TechnicalAnalysisAgent(
            weights=payload.weight_overrides or {},
            min_score=payload.min_score,
            disqualify_threshold=payload.disqualify_threshold,
            disqualify_max_weak=payload.disqualify_max_weak,
        )
        result = agent.run({
            "questions":          payload.questions,
            "supplier_responses": payload.supplier_responses,
        })
        result["project_id"] = payload.project_id
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gap")
async def gap_analysis(payload: GapAnalysisRequest):
    """FM-6.3 — Run gap analysis on pre-computed scores."""
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
            "project_id":   payload.project_id,
            "gaps":         gaps,
            "disqualified": disqualified,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/report")
async def generate_report(payload: ReportRequest):
    """FM-6.4 — Generate a narrative evaluation report for a single supplier."""
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
    """FM-6.2 — Return default weight categories for the UI sliders."""
    return {
        "categories": [
            {"key": "technical",  "label": "Technical Capability", "default_weight": 0.35},
            {"key": "quality",    "label": "Quality & Compliance",  "default_weight": 0.25},
            {"key": "delivery",   "label": "Delivery & Lead Time",  "default_weight": 0.20},
            {"key": "commercial", "label": "Commercial",            "default_weight": 0.20},
        ]
    }
