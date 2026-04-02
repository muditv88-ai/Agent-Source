"""
analysis.py  — Technical Analysis API routes  (v2: push_log instrumentation)

FM-6.1  Run AI scoring for all supplier responses
FM-6.2  Weight configurator  (per-session overrides)
FM-6.3  Gap analysis         (weak areas + disqualification)
FM-6.4  Narrative report     (per-supplier summary)
FM-6.5  Disqualification     (auto-flag suppliers below threshold)

URL namespace (prefix set ONLY in main.py):
  GET  /technical-analysis/status/{job_id}
  POST /technical-analysis/run
  POST /technical-analysis/gap
  POST /technical-analysis/report
  GET  /technical-analysis/weights/defaults
"""
import asyncio
import logging
import time
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
from app.api.routes.agent_logs import push_log

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Technical Analysis"])
_executor = ThreadPoolExecutor(max_workers=4)


# ── Request / Response models ──────────────────────────────────────────────

class RunAnalysisRequest(BaseModel):
    project_id: str
    questions: List[Dict[str, Any]] = Field(...)
    supplier_responses: Dict[str, Dict[str, str]] = Field(...)
    weight_overrides: Optional[Dict[str, float]] = Field(default=None)
    min_score: float = Field(default=4.0)
    disqualify_threshold: float = Field(default=2.0)
    disqualify_max_weak: int = Field(default=2)


class GapAnalysisRequest(BaseModel):
    project_id: str
    supplier_scores: Dict[str, Dict[str, Any]] = Field(...)
    questions: List[Dict[str, Any]]
    min_score: float = 4.0
    disqualify_threshold: float = 2.0
    disqualify_max_weak: int = 2


class ReportRequest(BaseModel):
    project_id: str
    supplier_name: str
    category_scores: List[Dict[str, Any]] = Field(default=[])
    overall_score: float


# ── Helpers ────────────────────────────────────────────────────────────────

def _overall(supplier_scores: Dict) -> float:
    vals = [
        float(v["score"])
        for v in supplier_scores.values()
        if isinstance(v, dict) and "score" in v
    ]
    return round(sum(vals) / max(len(vals), 1), 2) if vals else 0.0


def _shape_analysis_result(
    project_id, scores, gaps, reports, questions, supplier_responses, disqualified
) -> Dict[str, Any]:
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
            category_scores_out.append({"category": cat, "weighted_score": cat_avg, "questions": qs_out})

        overall = round(_overall(supplier_scores), 2)
        report  = reports.get(supplier_name, {})
        gap     = gaps.get(supplier_name, {})
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
    push_log(agent_id="technical", status="running",
             message="Loading RFP questions and supplier documents...")

    raw_qs = load_metadata(project_id, "questions.json") or []
    if not raw_qs:
        push_log(agent_id="technical", status="error",
                 message="No questions found — parse the RFP first")
        raise ValueError(
            "No questions found for this project. "
            "Please parse the RFP first (Upload RFP → Parse)."
        )

    supplier_paths = ensure_suppliers_local(project_id)
    if not supplier_paths:
        push_log(agent_id="technical", status="error",
                 message="No supplier files found — upload at least one response")
        raise ValueError(
            "No supplier files found. "
            "Please upload at least one supplier response."
        )

    name_meta = load_metadata(project_id, "suppliers.json") or {}
    supplier_responses: Dict[str, Dict[str, str]] = {}
    for path in supplier_paths:
        path = Path(path)
        supplier_name = (
            name_meta.get(str(path)) or name_meta.get(path.name) or path.stem
        )
        try:
            parsed    = parse_document(str(path))
            full_text = parsed.get("full_text", "")
        except Exception as e:
            logger.warning("Could not parse %s: %s", path, e)
            full_text = ""
        supplier_responses[supplier_name] = {
            q["question_id"]: full_text for q in raw_qs
        }

    if not supplier_responses:
        push_log(agent_id="technical", status="error",
                 message="All supplier documents failed to parse")
        raise ValueError("All supplier documents failed to parse.")

    push_log(agent_id="technical", status="running",
             message=f"Scoring {len(supplier_responses)} supplier(s) across {len(raw_qs)} questions...")

    t0     = time.time()
    agent  = TechnicalAnalysisAgent()
    result = agent.run({"questions": raw_qs, "supplier_responses": supplier_responses})
    duration_ms = int((time.time() - t0) * 1000)

    shaped = _shape_analysis_result(
        project_id=project_id,
        scores=result["scores"],
        gaps=result["gaps"],
        reports=result["reports"],
        questions=raw_qs,
        supplier_responses=supplier_responses,
        disqualified=result["disqualified"],
    )

    gap_count = len(result.get("disqualified", []))
    push_log(agent_id="technical", status="complete",
             message=f"Identified {gap_count} compliance gaps across {len(supplier_responses)} supplier(s)",
             confidence=87,
             duration_ms=duration_ms)
    return shaped


async def _run_analysis_job(project_id: str, job_id: str, _rfp_id: str = ""):
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
        push_log(agent_id="technical", status="error",
                 message=f"Analysis job failed: {type(e).__name__}")
        job_store.set_failed(job_id, str(e))
        update_module_state(project_id, "technical", "error")


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/status/{job_id}")
async def get_analysis_status(job_id: str):
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
    push_log(agent_id="technical", status="running",
             message=f"Scoring {len(payload.supplier_responses)} supplier(s)...")
    try:
        t0    = time.time()
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
        push_log(agent_id="technical", status="complete",
                 message=f"Scored {len(payload.supplier_responses)} supplier(s)",
                 confidence=87,
                 duration_ms=int((time.time() - t0) * 1000))
        return result
    except Exception as e:
        push_log(agent_id="technical", status="error", message=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gap")
async def gap_analysis(payload: GapAnalysisRequest):
    try:
        agent = TechnicalAnalysisAgent(
            min_score=payload.min_score,
            disqualify_threshold=payload.disqualify_threshold,
            disqualify_max_weak=payload.disqualify_max_weak,
        )
        gaps         = agent._gap_analysis(supplier_scores=payload.supplier_scores, questions=payload.questions)
        disqualified = [s for s, g in gaps.items() if g.get("disqualified")]
        push_log(agent_id="technical", status="complete",
                 message=f"Gap analysis complete — {len(disqualified)} disqualified")
        return {"project_id": payload.project_id, "gaps": gaps, "disqualified": disqualified}
    except Exception as e:
        push_log(agent_id="technical", status="error", message=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/report")
async def generate_report(payload: ReportRequest):
    try:
        agent  = TechnicalAnalysisAgent()
        report = agent._generate_report(
            supplier_name=payload.supplier_name,
            category_scores=payload.category_scores,
            overall_score=payload.overall_score,
        )
        push_log(agent_id="technical", status="complete",
                 message=f"Narrative report generated for {payload.supplier_name}")
        return {"project_id": payload.project_id, "report": report}
    except Exception as e:
        push_log(agent_id="technical", status="error", message=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/weights/defaults")
async def get_default_weights():
    return {
        "categories": [
            {"key": "technical",  "label": "Technical Capability", "default_weight": 0.35},
            {"key": "quality",    "label": "Quality & Compliance",  "default_weight": 0.25},
            {"key": "delivery",   "label": "Delivery & Lead Time",  "default_weight": 0.20},
            {"key": "commercial", "label": "Commercial",            "default_weight": 0.20},
        ]
    }
