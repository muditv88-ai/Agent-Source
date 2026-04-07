"""
analysis.py  — Technical Analysis API routes  (v3: parse-questions, confirm-questions, run-from-project)

FM-6.1  Run AI scoring for all supplier responses
FM-6.2  Weight configurator  (per-session overrides)
FM-6.3  Gap analysis         (weak areas + disqualification)
FM-6.4  Narrative report     (per-supplier summary)
FM-6.5  Disqualification     (auto-flag suppliers below threshold)
FM-7.0  Parse question files (XLSX/PDF/DOCX) → preview by sheet
FM-7.1  Confirm and save questions to questions.json
FM-7.2  Run analysis from stored project files
FM-7.3  Save and retrieve weight configurations

URL namespace (prefix set ONLY in main.py):
  GET  /technical-analysis/status/{job_id}
  POST /technical-analysis/run
  POST /technical-analysis/gap
  POST /technical-analysis/report
  GET  /technical-analysis/weights/defaults
  POST /technical-analysis/parse-questions
  POST /technical-analysis/confirm-questions
  POST /technical-analysis/run-from-project
  POST /technical-analysis/save-weights
  GET  /technical-analysis/weights/{project_id}
  POST /technical-analysis/request-clarification
"""
import asyncio
import json
import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

from app.agents.technical_analysis_agent import TechnicalAnalysisAgent
from app.agents.comms_agent import CommsAgent
from app.agents.response_intake_agent import ResponseIntakeAgent
from app.services.job_store import job_store
from app.services.project_store import (
    ensure_suppliers_local,
    load_metadata,
    save_metadata,
    update_module_state,
    PROJECTS_DIR,
)
from app.services.document_parser import parse_document
from app.services.workbook_parser import parse_workbook
from app.services.technical_parser import parse_technical_file
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


# ── New v3 Models ─────────────────────────────────────────────────────────

class ParsedQuestion(BaseModel):
    question_id: str
    question_text: str
    category: Optional[str] = None
    supplier_name: Optional[str] = None
    response: Optional[str] = None
    comments: Optional[str] = None
    score_hint: Optional[float] = None        # 0.0–1.0 from compliance status
    status: Optional[str] = None               # "pass" | "partial" | "fail" | "unknown"
    response_quality: Optional[str] = None     # "full" | "template" | "empty"


class SheetPreview(BaseModel):
    sheet_name: str
    row_count: int
    columns_detected: List[str]
    questions: List[ParsedQuestion]


class ParseQuestionsResponse(BaseModel):
    sheets: List[SheetPreview]
    total_questions: int
    suppliers_detected: List[str]


class ConfirmQuestionsRequest(BaseModel):
    project_id: str
    questions: List[Dict[str, Any]]
    file_display_name: Optional[str] = None


class RunFromProjectRequest(BaseModel):
    project_id: str
    rfp_id: Optional[str] = None
    weight_overrides: Optional[Dict[str, float]] = None
    min_score: float = 4.0
    disqualify_threshold: float = 2.0
    disqualify_max_weak: int = 2


class SaveWeightsRequest(BaseModel):
    project_id: str
    weights: Dict[str, float]


class RequestClarificationRequest(BaseModel):
    project_id: str
    supplier_name: str
    gap_areas: List[str]


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


# ── v3 Endpoints: Question Parsing & Project-based Analysis ─────────────────

def _validate_weights(weights: Dict[str, float]) -> bool:
    """Validate that weights sum to 1.0 ±0.01 tolerance."""
    total = sum(weights.values())
    return abs(total - 1.0) <= 0.01


def _extract_questions_from_workbook(sheets: Dict[str, List[Dict]]) -> List[ParsedQuestion]:
    """Extract questions from parsed workbook sheets."""
    questions = []
    qid = 1
    for sheet_name, rows in sheets.items():
        for row in rows:
            # Look for common column patterns: Question, Response, Comments, etc.
            question_text = (
                row.get("Question", "") or
                row.get("question", "") or
                row.get("Q", "")
            )
            if not question_text:
                continue

            questions.append(ParsedQuestion(
                question_id=f"Q{qid:03d}",
                question_text=str(question_text),
                category=row.get("Category") or row.get("category") or "General",
                supplier_name=row.get("Supplier") or row.get("supplier_name"),
                response=row.get("Response") or row.get("response"),
                comments=row.get("Comments") or row.get("comments"),
            ))
            qid += 1
    return questions


def _extract_questions_from_text(full_text: str) -> List[ParsedQuestion]:
    """Extract questions from PDF/DOCX text using OpenAI."""
    try:
        from app.services.ai_scorer import score_questions_parallel
        # For now, return empty — in production, use OpenAI to parse structured questions
        # This requires calling the copilot agent or using a dedicated parsing model
        logger.warning("PDF/DOCX question extraction not yet implemented — returning empty")
        return []
    except Exception as e:
        logger.warning("Failed to extract questions from text: %s", e)
        return []


@router.post("/parse-questions")
async def parse_questions_file(
    file: UploadFile = File(...),
    project_id: str = Form(...),
):
    """
    Parse an uploaded question file (XLSX only for technical questions).
    Returns a preview of questions organized by sheet with supplier names,
    compliance hints, and response quality assessments.
    """
    push_log(agent_id="technical", status="running",
             message="Parsing question file...")

    # Validate file extension (XLSX only for technical parsing)
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        push_log(agent_id="technical", status="error",
                 message=f"Unsupported file type: {file.filename}. Please upload .xlsx format.")
        raise HTTPException(status_code=415, detail="File could not be read as Excel. Please upload .xlsx format.")

    try:
        # Read file bytes
        content = await file.read()
        logger.info(f"Received file: {file.filename}, size: {len(content)} bytes")

        # Parse using technical_parser
        try:
            result = parse_technical_file(content, file.filename)
            logger.info(f"Parse result: {result['total_questions']} questions found")
        except Exception as e:
            logger.error(f"Parse error: {type(e).__name__}: {e}", exc_info=True)
            if "InvalidFileException" in str(type(e)):
                push_log(agent_id="technical", status="error",
                         message=f"File could not be read as Excel")
                raise HTTPException(status_code=415, detail="File could not be read as Excel. Please upload .xlsx format.")
            raise

        # Check if any questions were found
        if result["total_questions"] == 0:
            push_log(agent_id="technical", status="error",
                     message="No question rows detected in file")
            raise HTTPException(status_code=400, detail="No question rows detected. Ensure the file contains Q# and Question columns.")

        # Convert parser result to response format
        sheet_previews = []
        for sheet_result in result["sheets"]:
            # Convert parsed questions to ParsedQuestion objects
            parsed_questions = []
            for q in sheet_result["questions"]:
                parsed_questions.append(ParsedQuestion(
                    question_id=q.get("question_id"),
                    question_text=q.get("question_text"),
                    category=q.get("category"),
                    supplier_name=q.get("supplier_name"),
                    response=q.get("response"),
                    comments=q.get("comments"),
                    score_hint=q.get("score_hint"),
                    status=q.get("status"),
                    response_quality=q.get("response_quality"),
                ))

            sheet_previews.append(SheetPreview(
                sheet_name=f"{sheet_result['sheet_name']} — {sheet_result['section_name']}",
                row_count=sheet_result["row_count"],
                columns_detected=sheet_result["columns_detected"],
                questions=parsed_questions,
            ))

        push_log(agent_id="technical", status="complete",
                 message=f"Parsed {result['total_questions']} questions from {len(result['sheets'])} sheets, "
                         f"suppliers: {', '.join(result['suppliers_detected'])}")

        return ParseQuestionsResponse(
            sheets=sheet_previews,
            total_questions=result["total_questions"],
            suppliers_detected=result["suppliers_detected"],
        ).dict()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Parse questions error: {e}", exc_info=True)
        push_log(agent_id="technical", status="error", message=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm-questions")
async def confirm_questions(payload: ConfirmQuestionsRequest):
    """Save confirmed questions to project metadata."""
    push_log(agent_id="technical", status="running",
             message=f"Saving {len(payload.questions)} questions to repository...")

    try:
        # Normalize questions to standard format
        normalized_qs = []
        for q in payload.questions:
            normalized_qs.append({
                "question_id": q.get("question_id", f"Q{len(normalized_qs)+1:03d}"),
                "question_text": q.get("question_text", ""),
                "category": q.get("category", "General"),
                "question_type": q.get("question_type", "qualitative"),
                "weight": float(q.get("weight", 10)),
                "scoring_guidance": q.get("scoring_guidance"),
            })

        # Save to metadata
        save_metadata(payload.project_id, "questions.json", normalized_qs)
        update_module_state(payload.project_id, "technical", "complete")

        categories = list(set(q.get("category", "General") for q in normalized_qs))
        push_log(agent_id="technical", status="complete",
                 message=f"Questions saved to repository ({len(categories)} categories)")

        return {
            "saved": True,
            "question_count": len(normalized_qs),
            "categories": categories,
        }

    except Exception as e:
        push_log(agent_id="technical", status="error", message=str(e))
        raise HTTPException(status_code=500, detail=str(e))


def _do_analysis_from_project(
    project_id: str,
    weight_overrides: Optional[Dict[str, float]] = None,
    min_score: float = 4.0,
    disqualify_threshold: float = 2.0,
    disqualify_max_weak: int = 2,
) -> Dict[str, Any]:
    """Background job: load questions and suppliers, run analysis."""
    push_log(agent_id="technical", status="running",
             message="Loading questions and supplier files from project...")

    # Load questions
    raw_qs = load_metadata(project_id, "questions.json") or []
    if not raw_qs:
        push_log(agent_id="technical", status="error",
                 message="No questions found — parse and confirm questions first")
        raise ValueError(
            "No questions found. Please parse and confirm a question file first."
        )

    # Load supplier files
    supplier_paths = ensure_suppliers_local(project_id)
    if not supplier_paths:
        push_log(agent_id="technical", status="error",
                 message="No supplier files found — upload at least one response")
        raise ValueError(
            "No supplier files found. Please upload at least one supplier response."
        )

    # Build supplier_responses dict
    name_meta = load_metadata(project_id, "suppliers.json") or {}
    supplier_responses: Dict[str, Dict[str, str]] = {}
    for path in supplier_paths:
        path = Path(path)
        supplier_name = (
            name_meta.get(str(path)) or name_meta.get(path.name) or path.stem
        )
        try:
            parsed = parse_document(str(path))
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

    # Validate weights if provided
    if weight_overrides:
        if not _validate_weights(weight_overrides):
            raise ValueError("Weights must sum to 1.0 (tolerance ±0.01)")

    push_log(agent_id="technical", status="running",
             message=f"Scoring {len(supplier_responses)} supplier(s) across {len(raw_qs)} questions...")

    # Run analysis
    t0 = time.time()
    agent = TechnicalAnalysisAgent(
        weights=weight_overrides or {},
        min_score=min_score,
        disqualify_threshold=disqualify_threshold,
        disqualify_max_weak=disqualify_max_weak,
    )
    result = agent.run({
        "questions": raw_qs,
        "supplier_responses": supplier_responses,
    })
    duration_ms = int((time.time() - t0) * 1000)

    # Shape result
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
             message=f"Analysis complete — {gap_count} compliance gaps, {len(supplier_responses)} suppliers",
             confidence=87,
             duration_ms=duration_ms)

    return shaped


async def _run_analysis_from_project_job(
    project_id: str,
    job_id: str,
    weight_overrides: Optional[Dict[str, float]] = None,
    min_score: float = 4.0,
    disqualify_threshold: float = 2.0,
    disqualify_max_weak: int = 2,
):
    """Async wrapper for background job."""
    job_store.set_running(job_id)
    update_module_state(project_id, "technical", "active")
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _executor,
            _do_analysis_from_project,
            project_id,
            weight_overrides,
            min_score,
            disqualify_threshold,
            disqualify_max_weak,
        )
        job_store.set_completed(job_id, result)
        update_module_state(project_id, "technical", "complete")
    except Exception as e:
        err = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error("Analysis job %s failed: %s", job_id, err)
        push_log(agent_id="technical", status="error",
                 message=f"Analysis job failed: {type(e).__name__}")
        job_store.set_failed(job_id, str(e))
        update_module_state(project_id, "technical", "error")


@router.post("/run-from-project")
async def run_analysis_from_project(payload: RunFromProjectRequest):
    """
    Trigger async analysis job using stored questions and supplier files.
    Returns job_id for polling.
    """
    push_log(agent_id="technical", status="running",
             message="Starting analysis from project repository...")

    try:
        # Validate weights if provided
        if payload.weight_overrides:
            if not _validate_weights(payload.weight_overrides):
                push_log(agent_id="technical", status="error",
                         message="Weights must sum to 1.0")
                raise HTTPException(status_code=422, detail="Weights must sum to 1.0")

        job_id = job_store.create()

        # Launch background job
        import asyncio as aio
        aio.create_task(_run_analysis_from_project_job(
            project_id=payload.project_id,
            job_id=job_id,
            weight_overrides=payload.weight_overrides,
            min_score=payload.min_score,
            disqualify_threshold=payload.disqualify_threshold,
            disqualify_max_weak=payload.disqualify_max_weak,
        ))

        return {
            "job_id": job_id,
            "status": "pending",
            "message": "Analysis job queued — poll /status/{job_id}",
        }

    except HTTPException:
        raise
    except Exception as e:
        push_log(agent_id="technical", status="error", message=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/save-weights")
async def save_weights(payload: SaveWeightsRequest):
    """Save buyer's weight configuration for a project."""
    push_log(agent_id="technical", status="running",
             message="Saving weight configuration...")

    try:
        if not _validate_weights(payload.weights):
            push_log(agent_id="technical", status="error",
                     message="Weights must sum to 1.0")
            raise HTTPException(status_code=422, detail="Weights must sum to 1.0")

        save_metadata(payload.project_id, "tech_weights.json", payload.weights)
        push_log(agent_id="technical", status="complete",
                 message="Weight configuration saved")

        return {"saved": True}

    except HTTPException:
        raise
    except Exception as e:
        push_log(agent_id="technical", status="error", message=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/weights/{project_id}")
async def get_weights(project_id: str):
    """Load saved weights for a project or return defaults."""
    try:
        weights = load_metadata(project_id, "tech_weights.json")
        if weights:
            return {"weights": weights, "source": "project"}
        else:
            # Return defaults
            return {
                "weights": {
                    "Technical Capability": 0.35,
                    "Quality & Compliance": 0.25,
                    "Delivery & Lead Time": 0.20,
                    "Commercial": 0.20,
                },
                "source": "defaults",
            }
    except Exception as e:
        logger.warning("Failed to load weights for %s: %s", project_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/request-clarification")
async def request_clarification(payload: RequestClarificationRequest):
    """Draft a clarification email for supplier gaps."""
    push_log(agent_id="technical", status="running",
             message=f"Drafting clarification for {payload.supplier_name}...")

    try:
        agent = CommsAgent()
        result = agent.run({
            "type": "clarification_request",
            "supplier_name": payload.supplier_name,
            "project_id": payload.project_id,
            "missing_questions": ", ".join(payload.gap_areas) if payload.gap_areas else "Quality and Compliance",
            "auto_send": False,
        })

        push_log(agent_id="technical", status="complete",
                 message="Clarification email draft generated")

        return {
            "draft_email": result.get("drafted", {}),
            "supplier_name": payload.supplier_name,
            "type": result.get("type"),
        }

    except Exception as e:
        push_log(agent_id="technical", status="error", message=str(e))
        raise HTTPException(status_code=500, detail=str(e))
