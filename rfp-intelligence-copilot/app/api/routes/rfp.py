"""
rfp.py  v2.0

ALL EXISTING ENDPOINTS PRESERVED.
New endpoint added:
  GET /rfp/{project_id}/structured-view
    Returns RFP decomposed into:
      - supplier_info[]        (contact/header fields)
      - technical_questions[]  (from questions.json)
      - pricing_fields[]       (from pricing parser structure_info)
    Reads from already-parsed metadata — no re-parsing triggered.
    Falls back gracefully if data is not yet available.
"""
import os
import json
import traceback
import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Form, BackgroundTasks

from app.services.project_store import (
    get_project, load_metadata, save_metadata,
    ensure_rfp_local, save_rfp_file, update_project_meta,
    update_module_state,
)
from app.services.job_store import job_store, JobStatus
from app.services.document_parser import parse_document
from app.services.rfp_extractor import extract_rfp_questions
from app.models.schemas import RFPQuestion, RFPStructuredView, SupplierInfoField, TechnicalQuestionField, PricingField

router = APIRouter()
ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".pdf", ".docx"}
_executor = ThreadPoolExecutor(max_workers=4)


# ════════════════════════════════════════════════════════════════════════════
# EXISTING ENDPOINTS — UNCHANGED
# ════════════════════════════════════════════════════════════════════════════

@router.post("/upload/{project_id}")
async def upload_rfp(project_id: str, file: UploadFile = File(...)):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")
    data = await file.read()
    save_rfp_file(project_id, file.filename, data)
    update_project_meta(project_id, rfp_filename=file.filename, status="rfp_uploaded")
    update_module_state(project_id, "rfp", "active")
    return {"project_id": project_id, "filename": file.filename, "status": "rfp_uploaded"}


def _do_parse_rfp(project_id: str) -> dict:
    rfp_path = ensure_rfp_local(project_id)
    if not rfp_path:
        raise FileNotFoundError("No RFP file found")
    parsed   = parse_document(str(rfp_path))
    full_text = parsed.get("full_text", "")
    extracted = extract_rfp_questions(full_text)
    raw_qs    = extracted.get("questions", [])
    questions = [
        RFPQuestion(
            question_id   = q["question_id"],
            category      = q["category"],
            question_text = q["question_text"],
            question_type = q.get("question_type", "qualitative"),
            weight        = float(q.get("weight", 10)),
            scoring_guidance = q.get("scoring_guidance"),
        )
        for q in raw_qs
    ]
    save_metadata(project_id, "questions.json", [q.dict() for q in questions])
    update_project_meta(project_id, status="parsed")
    update_module_state(project_id, "rfp", "complete")
    return {
        "project_id":      project_id,
        "status":          "parsed",
        "questions":       [q.dict() for q in questions],
        "categories":      extracted.get("categories", []),
        "total_questions": len(questions),
    }


async def _run_parse_rfp(project_id: str, job_id: str):
    job_store.set_running(job_id)
    try:
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(_executor, _do_parse_rfp, project_id)
        job_store.set_completed(job_id, result)
    except Exception as e:
        job_store.set_failed(job_id, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        update_module_state(project_id, "rfp", "error")


@router.post("/parse/{project_id}")
async def parse_rfp(project_id: str, background_tasks: BackgroundTasks):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    job_id = job_store.create()
    background_tasks.add_task(_run_parse_rfp, project_id, job_id)
    return {"job_id": job_id, "status": JobStatus.PENDING}


@router.get("/parse-status/{job_id}")
async def get_parse_status(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "status": job["status"],
            "result": job.get("result"), "error": job.get("error")}


@router.get("/{project_id}")
async def get_rfp_data(project_id: str):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    questions_raw = load_metadata(project_id, "questions.json") or []
    return {
        "project_id": project_id,
        "rfp_filename": project.get("rfp_filename"),
        "status": project.get("status"),
        "questions": questions_raw,
        "total_questions": len(questions_raw),
    }


# ════════════════════════════════════════════════════════════════════════════
# NEW v2.0 — Structured view
# ════════════════════════════════════════════════════════════════════════════

# Standard supplier info fields expected in most RFP templates
_SUPPLIER_INFO_FIELD_NAMES = [
    "Company Name", "Contact Name", "Contact Email", "Contact Phone",
    "Registered Address", "Company Registration Number",
    "VAT / GST Number", "Year Established", "Annual Turnover",
]


@router.get("/{project_id}/structured-view")
async def get_rfp_structured_view(project_id: str):
    """
    Return a structured decomposition of the RFP into three sections:
      1. supplier_info      — standard supplier header fields
      2. technical_questions — from questions.json (already parsed)
      3. pricing_fields     — from pricing analysis structure_info

    Reads only from already-persisted metadata — no heavy processing.
    Returns a partial view if some sections are not yet available.
    """
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    warnings = []

    # ── 1. Supplier info fields (static template — always available) ──
    supplier_info = [
        SupplierInfoField(field_name=f, editable=True)
        for f in _SUPPLIER_INFO_FIELD_NAMES
    ]

    # ── 2. Technical questions from parsed questions.json ──
    questions_raw = load_metadata(project_id, "questions.json") or []
    if not questions_raw:
        warnings.append("Technical questions not yet parsed — run Parse RFP first")
    technical_questions = [
        TechnicalQuestionField(
            question_id      = q.get("question_id", ""),
            question_text    = q.get("question_text", ""),
            category         = q.get("category", ""),
            weight           = float(q.get("weight", 10)),
            question_type    = q.get("question_type", "qualitative"),
            scoring_guidance = q.get("scoring_guidance"),
        )
        for q in questions_raw
    ]

    # ── 3. Pricing fields from pricing analysis results ──
    pricing_result   = load_metadata(project_id, "pricing_result.json") or {}
    structure_info   = pricing_result.get("structure_info", {})
    currency         = pricing_result.get("currency", "")
    structure_type   = pricing_result.get("structure_type", "")
    buyer_fields     = structure_info.get("buyer_fields", [])
    supplier_fields  = structure_info.get("supplier_fields", [])
    line_items       = pricing_result.get("all_line_items", [])

    if not line_items:
        warnings.append("Pricing fields not yet available — run Pricing Analysis first")

    pricing_fields = []
    for item in line_items:
        field_type = "buyer_defined" if item.get("is_buyer_defined") else "supplier_filled"
        pricing_fields.append(
            PricingField(
                field_name  = item.get("description", ""),
                field_type  = field_type,
                sku         = item.get("sku", "") or None,
                description = item.get("description", ""),
                quantity    = item.get("quantity"),
                unit        = item.get("unit", "") or None,
                category    = item.get("category", "") or None,
            )
        )

    return RFPStructuredView(
        project_id          = project_id,
        rfp_filename        = project.get("rfp_filename"),
        supplier_info       = supplier_info,
        technical_questions = technical_questions,
        pricing_fields      = pricing_fields,
        structure_type      = structure_type or None,
        currency            = currency or None,
        parse_warnings      = pricing_result.get("parse_warnings", []) + warnings,
        cached              = bool(questions_raw or line_items),
    ).dict()
