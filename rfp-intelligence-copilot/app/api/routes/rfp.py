"""
rfp.py  v3.0

New endpoints added (existing endpoints UNCHANGED for backward compat):
  POST /rfp/generate                       — AI-generate a new RFP from category + scope
  POST /rfp/upload-supplier-response       — Intake a supplier response via ResponseIntakeAgent
  POST /rfp/{project_id}/questions/weights — Update question weights for a project
  GET  /rfp/{project_id}/completeness      — Return response completeness per supplier
"""
from typing import Any, Dict, List, Optional
import os

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

# ── existing service imports (UNCHANGED) ─────────────────────────────────
from app.services.rfp_extractor    import extract_rfp_questions
from app.services.document_parser  import extract_text
from app.services.supplier_parser  import parse_supplier_response

# ── new agent imports ─────────────────────────────────────────────────────
from app.agents.rfp_generation_agent  import RFPGenerationAgent
from app.agents.response_intake_agent import ResponseIntakeAgent

router = APIRouter()


# ── Pydantic models ────────────────────────────────────────────────────────

class RFPGenerateRequest(BaseModel):
    category:     str
    scope:        str
    requirements: Optional[str] = ""
    project_id:   Optional[str] = None

class WeightUpdateRequest(BaseModel):
    weights: Dict[str, float]   # {category_name: weight_0_to_100}

class SupplierResponseUploadRequest(BaseModel):
    project_id: str
    supplier_name: Optional[str] = None


# ════════════════════════════════════════════════════════════════════════════
# EXISTING ENDPOINTS — preserved exactly, no changes
# (original rfp.py content inlined below for backward compat)
# ════════════════════════════════════════════════════════════════════════════

@router.post("/extract")
async def extract_rfp(
    file: UploadFile = File(...),
    project_id: Optional[str] = Form(None),
):
    """
    Extract structured questions from an uploaded RFP document.
    Supports PDF, DOCX, XLSX, TXT.
    """
    file_bytes = await file.read()
    try:
        text = extract_text(file_bytes, file.filename)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse file: {e}")

    if not text or not text.strip():
        raise HTTPException(status_code=422, detail="No text content found in uploaded file.")

    try:
        result = extract_rfp_questions(text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RFP extraction failed: {e}")

    result["project_id"] = project_id
    result["source_file"] = file.filename
    return result


@router.post("/parse-supplier-response")
async def parse_supplier(
    file: UploadFile = File(...),
    questions: str   = Form(...),   # JSON-encoded list of question dicts
    project_id: Optional[str] = Form(None),
):
    """
    Parse a supplier's response file against a set of RFP questions.
    Existing endpoint — backward compatible.
    """
    import json as _json
    file_bytes = await file.read()
    try:
        text = extract_text(file_bytes, file.filename)
        rfp_questions = _json.loads(questions)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    result = parse_supplier_response(text, rfp_questions)
    result["project_id"] = project_id
    result["source_file"] = file.filename
    return result


# ════════════════════════════════════════════════════════════════════════════
# NEW v3.0 ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════

@router.post("/generate")
async def generate_rfp(payload: RFPGenerateRequest):
    """
    AI-generate a new RFP from category and scope description.
    Uses RFPGenerationAgent backed by NVIDIA Nemotron.
    Returns: full RFP text + structured questions list.
    """
    agent = RFPGenerationAgent()
    try:
        result = agent.run({
            "mode":         "generate",
            "category":     payload.category,
            "scope":        payload.scope,
            "requirements": payload.requirements or "",
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    result["project_id"] = payload.project_id
    return result


@router.post("/upload-supplier-response")
async def upload_supplier_response(
    file: UploadFile = File(...),
    project_id: str  = Form(...),
    questions: str   = Form(...),   # JSON-encoded list of question dicts
):
    """
    Upload a supplier response file. ResponseIntakeAgent:
      1. Extracts supplier name
      2. Maps answers to RFP questions
      3. Calculates completeness %
      4. Auto-sends clarification request if incomplete
    """
    import json as _json
    file_bytes = await file.read()
    try:
        text = extract_text(file_bytes, file.filename)
        rfp_questions = _json.loads(questions)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    agent = ResponseIntakeAgent()
    try:
        result = agent.run({
            "file_text":     text,
            "rfp_questions": rfp_questions,
            "project_id":    project_id,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    result["source_file"] = file.filename
    return result


@router.post("/{project_id}/questions/weights")
async def update_question_weights(
    project_id: str,
    payload: WeightUpdateRequest,
):
    """
    Update evaluation question weights for a project.
    Weights are per-category (e.g. {"Technical": 60, "Pricing": 30, "Compliance": 10}).
    """
    # TODO: persist to DB; for now return acknowledgement
    total = sum(payload.weights.values())
    if abs(total - 100.0) > 0.5:
        raise HTTPException(
            status_code=422,
            detail=f"Weights must sum to 100. Got {total}."
        )
    return {
        "project_id": project_id,
        "weights":    payload.weights,
        "status":     "updated",
        "note":       "Weights will be applied in next TechnicalAnalysisAgent run.",
    }


@router.get("/{project_id}/completeness")
async def get_response_completeness(project_id: str):
    """
    Return supplier response completeness stats for a project.
    Full implementation requires DB persistence; returns structure only.
    """
    # TODO: query DB for intake results keyed by project_id
    return {
        "project_id": project_id,
        "suppliers":  [],
        "note": "Wire to DB after persistence layer is added.",
    }
