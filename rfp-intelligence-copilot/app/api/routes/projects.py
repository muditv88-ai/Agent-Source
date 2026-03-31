"""
projects.py  v2.0

ALL EXISTING ENDPOINTS ARE PRESERVED EXACTLY.
New endpoints appended at the bottom — no routes renamed or removed.

New endpoints (v2.0):
  PATCH  /projects/{id}/meta              — update optional project metadata
  GET    /projects/{id}/module-states     — read module completion states
  PATCH  /projects/{id}/module-states     — update one module state
  GET    /projects/{id}/audit-log         — read chatbot/action audit trail
  GET    /projects/{id}/feature-flags     — read feature flags
  PATCH  /projects/{id}/feature-flags     — toggle feature flags
"""
import json
import traceback
import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Form, BackgroundTasks

from app.services.project_store import (
    create_project, get_project, list_projects, delete_project,
    get_rfp_path, get_supplier_paths,
    get_questions_path, get_suppliers_meta_path,
    update_project_status, update_project_meta,
    save_rfp_file, save_supplier_file, save_metadata, load_metadata,
    ensure_rfp_local, ensure_suppliers_local,
    delete_supplier_file, is_gcs_enabled,
    PROJECTS_DIR,
    # v2.0
    get_module_states, update_module_state,
    get_feature_flags, set_feature_flags,
    load_audit_log,
)
from app.services.job_store import job_store, JobStatus
from app.services.document_parser import parse_document
from app.services.rfp_extractor import extract_rfp_questions
from app.models.schemas import (
    RFPQuestion,
    # v2.0
    ProjectMetaUpdateRequest,
    ModuleStateUpdateRequest,
    FeatureFlagUpdateRequest,
)

router = APIRouter()

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".pdf", ".docx"}
_executor = ThreadPoolExecutor(max_workers=10)


# ════════════════════════════════════════════════════════════════════════════
# EXISTING ENDPOINTS — UNCHANGED
# ════════════════════════════════════════════════════════════════════════════

@router.get("/parse-status/{job_id}")
async def get_project_parse_status(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "status": job["status"],
            "result": job.get("result"), "error": job.get("error")}


@router.post("", status_code=201)
async def create_new_project(name: str = Form(...)):
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Project name is required")
    return create_project(name.strip())


@router.get("")
async def list_all_projects():
    return {"projects": list_projects()}


@router.get("/{project_id}")
async def get_project_detail(project_id: str):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    meta = load_metadata(project_id, "suppliers.json") or {}
    project["suppliers"] = [{"path": k, "name": v} for k, v in meta.items()]
    project["storage_backend"] = "gcs" if is_gcs_enabled() else "local"
    return project


@router.delete("/{project_id}")
async def delete_project_endpoint(project_id: str):
    deleted = delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project_id": project_id, "deleted": True}


@router.post("/{project_id}/rfp")
async def upload_project_rfp(project_id: str, file: UploadFile = File(...)):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{suffix}'")
    data = await file.read()
    save_rfp_file(project_id, file.filename, data)
    update_project_meta(project_id, rfp_filename=file.filename, status="rfp_uploaded")
    # v2.0: advance RFP module state
    update_module_state(project_id, "rfp", "active")
    return {"project_id": project_id, "rfp_filename": file.filename, "status": "rfp_uploaded"}


@router.post("/{project_id}/supplier")
async def upload_project_supplier(
    project_id: str,
    file: UploadFile = File(...),
    supplier_name: Optional[str] = Form(None),
):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{suffix}'")
    data = await file.read()
    local_path   = save_supplier_file(project_id, file.filename, data)
    resolved_name = (supplier_name or "").strip() or Path(file.filename).stem
    meta = load_metadata(project_id, "suppliers.json") or {}
    meta[str(local_path)] = resolved_name
    save_metadata(project_id, "suppliers.json", meta)
    update_project_meta(project_id, status="suppliers_uploaded")
    return {
        "project_id":       project_id,
        "supplier_filename": file.filename,
        "supplier_name":    resolved_name,
        "status":           "supplier_uploaded",
    }


@router.delete("/{project_id}/supplier/{filename}")
async def remove_project_supplier(project_id: str, filename: str):
    deleted = delete_supplier_file(project_id, filename)
    if not deleted:
        raise HTTPException(status_code=404, detail="Supplier file not found")
    meta = load_metadata(project_id, "suppliers.json") or {}
    keys_to_remove = [k for k in meta if Path(k).name == filename]
    for k in keys_to_remove:
        del meta[k]
    save_metadata(project_id, "suppliers.json", meta)
    return {"deleted": filename}


def _do_parse_project(project_id: str) -> dict:
    rfp_path = ensure_rfp_local(project_id)
    if not rfp_path:
        raise FileNotFoundError("No RFP file found in project")
    parsed_doc = parse_document(str(rfp_path))
    full_text  = parsed_doc.get("full_text", "")
    extracted  = extract_rfp_questions(full_text)
    raw_qs     = extracted.get("questions", [])
    questions  = [
        RFPQuestion(
            question_id=q["question_id"],
            category=q["category"],
            question_text=q["question_text"],
            question_type=q.get("question_type", "qualitative"),
            weight=float(q.get("weight", 10)),
            scoring_guidance=q.get("scoring_guidance"),
        )
        for q in raw_qs
    ]
    save_metadata(project_id, "questions.json", [q.dict() for q in questions])
    update_project_meta(project_id, status="parsed")
    # v2.0: mark RFP module complete
    update_module_state(project_id, "rfp", "complete")
    return {
        "project_id":      project_id,
        "status":          "parsed",
        "questions":       [q.dict() for q in questions],
        "categories":      extracted.get("categories", []),
        "total_questions": len(questions),
    }


async def _run_parse_project(project_id: str, job_id: str):
    job_store.set_running(job_id)
    try:
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(_executor, _do_parse_project, project_id)
        job_store.set_completed(job_id, result)
    except Exception as e:
        job_store.set_failed(job_id, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        update_module_state(project_id, "rfp", "error")


@router.post("/{project_id}/parse")
async def parse_project_rfp(project_id: str, background_tasks: BackgroundTasks):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    job_id = job_store.create()
    background_tasks.add_task(_run_parse_project, project_id, job_id)
    return {"job_id": job_id, "status": JobStatus.PENDING}


@router.post("/{project_id}/analyze")
async def analyze_project(project_id: str, background_tasks: BackgroundTasks):
    from app.api.routes.analysis import _run_analysis_job
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    job_id = job_store.create()
    background_tasks.add_task(_run_analysis_job, project_id, job_id, project_id)
    return {"job_id": job_id, "status": JobStatus.PENDING}


# ════════════════════════════════════════════════════════════════════════════
# NEW v2.0 ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════

@router.patch("/{project_id}/meta")
async def update_project_metadata(project_id: str, body: ProjectMetaUpdateRequest):
    """
    Update optional project metadata fields.
    Only provided (non-None) fields are written — all others are preserved.
    """
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        return {"project_id": project_id, "updated": {}, "message": "No fields to update"}
    update_project_meta(project_id, **updates)
    return {"project_id": project_id, "updated": updates}


@router.get("/{project_id}/module-states")
async def get_project_module_states(project_id: str):
    """
    Return completion state of each module for this project.
    States: pending | active | complete | error
    """
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "project_id":    project_id,
        "module_states": get_module_states(project_id),
    }


@router.patch("/{project_id}/module-states")
async def set_project_module_state(project_id: str, body: ModuleStateUpdateRequest):
    """
    Update the state of one module.
    module: rfp | technical | pricing
    state:  pending | active | complete | error
    """
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        updated = update_module_state(project_id, body.module, body.state)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"project_id": project_id, "module_states": updated}


@router.get("/{project_id}/audit-log")
async def get_project_audit_log(project_id: str, limit: int = 50):
    """
    Return the most recent audit log entries for this project.
    Entries are written by the chatbot and pricing scenario engine.
    """
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    entries = load_audit_log(project_id, limit=min(limit, 200))
    return {"project_id": project_id, "entries": entries, "count": len(entries)}


@router.get("/{project_id}/feature-flags")
async def get_project_feature_flags(project_id: str):
    """
    Return all feature flags for this project.
    Unset flags return their safe defaults.
    """
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "project_id":   project_id,
        "feature_flags": get_feature_flags(project_id),
    }


@router.patch("/{project_id}/feature-flags")
async def update_project_feature_flags(project_id: str, body: FeatureFlagUpdateRequest):
    """
    Toggle one or more feature flags for this project.
    Only provided (non-None) fields are updated.
    """
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        return {"project_id": project_id, "feature_flags": get_feature_flags(project_id)}
    updated = set_feature_flags(project_id, updates)
    return {"project_id": project_id, "feature_flags": updated, "changed": updates}
