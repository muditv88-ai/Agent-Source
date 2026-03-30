import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Form, BackgroundTasks
from typing import Optional
from app.services.project_store import (
    create_project,
    get_project,
    list_projects,
    delete_project,
    get_rfp_path,
    get_supplier_paths,
    get_suppliers_meta_path,
    update_project_status,
    update_project_meta,
    PROJECTS_DIR,
)
from app.services.job_store import job_store, JobStatus
from app.services.document_parser import parse_document
from app.services.rfp_extractor import extract_rfp_questions
from app.models.schemas import RFPQuestion
import asyncio
import json
import traceback
from concurrent.futures import ThreadPoolExecutor

router = APIRouter()

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".pdf", ".docx"}
_executor = ThreadPoolExecutor(max_workers=10)


# ── Create project ───────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_new_project(name: str = Form(...)):
    """Create a new project container. Returns project_id."""
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Project name is required")
    project = create_project(name.strip())
    return project


# ── List projects ────────────────────────────────────────────────────────────

@router.get("")
async def list_all_projects():
    """Return all projects ordered by most recently modified."""
    return {"projects": list_projects()}


# ── Get single project ───────────────────────────────────────────────────────

@router.get("/{project_id}")
async def get_project_detail(project_id: str):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    # Attach supplier names
    suppliers_meta_path = get_suppliers_meta_path(project_id)
    suppliers = []
    if suppliers_meta_path.exists():
        raw = json.loads(suppliers_meta_path.read_text())
        suppliers = [{"path": k, "name": v} for k, v in raw.items()]
    project["suppliers"] = suppliers
    return project


# ── Delete project ───────────────────────────────────────────────────────────

@router.delete("/{project_id}")
async def delete_project_endpoint(project_id: str):
    deleted = delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project_id": project_id, "deleted": True}


# ── Upload RFP into project ──────────────────────────────────────────────────

@router.post("/{project_id}/rfp")
async def upload_project_rfp(project_id: str, file: UploadFile = File(...)):
    """Upload the RFP document for a project (replaces existing if any)."""
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{suffix}'")

    rfp_dir = PROJECTS_DIR / project_id / "rfp"
    # Clear old RFP
    for old in rfp_dir.iterdir():
        old.unlink()

    dest = rfp_dir / file.filename
    with dest.open("wb") as buf:
        import shutil as _sh
        _sh.copyfileobj(file.file, buf)

    update_project_meta(project_id, rfp_filename=file.filename, status="rfp_uploaded")
    return {"project_id": project_id, "rfp_filename": file.filename, "status": "rfp_uploaded"}


# ── Upload supplier into project ─────────────────────────────────────────────

@router.post("/{project_id}/supplier")
async def upload_project_supplier(
    project_id: str,
    file: UploadFile = File(...),
    supplier_name: Optional[str] = Form(None),
):
    """Upload a supplier response file. Can be called multiple times."""
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{suffix}'")

    supplier_dir = PROJECTS_DIR / project_id / "suppliers"
    dest = supplier_dir / file.filename
    with dest.open("wb") as buf:
        import shutil as _sh
        _sh.copyfileobj(file.file, buf)

    resolved_name = (supplier_name or "").strip() or Path(file.filename).stem

    # Persist supplier name mapping
    suppliers_meta_path = get_suppliers_meta_path(project_id)
    meta = json.loads(suppliers_meta_path.read_text()) if suppliers_meta_path.exists() else {}
    meta[str(dest)] = resolved_name
    suppliers_meta_path.write_text(json.dumps(meta, indent=2))

    update_project_meta(project_id, status="suppliers_uploaded")
    return {
        "project_id": project_id,
        "supplier_filename": file.filename,
        "supplier_name": resolved_name,
        "status": "supplier_uploaded",
    }


# ── Remove a supplier from project ──────────────────────────────────────────

@router.delete("/{project_id}/supplier/{filename}")
async def remove_project_supplier(project_id: str, filename: str):
    supplier_dir = PROJECTS_DIR / project_id / "suppliers"
    target = supplier_dir / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail="Supplier file not found")
    target.unlink()
    # Remove from metadata
    suppliers_meta_path = get_suppliers_meta_path(project_id)
    if suppliers_meta_path.exists():
        meta = json.loads(suppliers_meta_path.read_text())
        meta.pop(str(target), None)
        suppliers_meta_path.write_text(json.dumps(meta, indent=2))
    return {"deleted": filename}


# ── Parse RFP from project files ─────────────────────────────────────────────

def _do_parse_project(project_id: str) -> dict:
    rfp_path = get_rfp_path(project_id)
    if not rfp_path:
        raise FileNotFoundError("No RFP file found in project")

    parsed_doc = parse_document(str(rfp_path))
    full_text = parsed_doc.get("full_text", "")
    extracted = extract_rfp_questions(full_text)
    raw_questions = extracted.get("questions", [])

    questions = [
        RFPQuestion(
            question_id=q["question_id"],
            category=q["category"],
            question_text=q["question_text"],
            question_type=q.get("question_type", "qualitative"),
            weight=float(q.get("weight", 10)),
            scoring_guidance=q.get("scoring_guidance"),
        )
        for q in raw_questions
    ]

    # Persist to project metadata
    from app.services.project_store import get_questions_path
    q_path = get_questions_path(project_id)
    q_path.write_text(json.dumps([q.dict() for q in questions]))
    update_project_meta(project_id, status="parsed")

    return {
        "project_id": project_id,
        "status": "parsed",
        "questions": [q.dict() for q in questions],
        "categories": extracted.get("categories", []),
        "total_questions": len(questions),
    }


async def _run_parse_project(project_id: str, job_id: str):
    job_store.set_running(job_id)
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(_executor, _do_parse_project, project_id)
        job_store.set_completed(job_id, result)
    except Exception as e:
        job_store.set_failed(job_id, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


@router.post("/{project_id}/parse")
async def parse_project_rfp(project_id: str, background_tasks: BackgroundTasks):
    """Re-parse the stored RFP. No file upload needed."""
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not get_rfp_path(project_id):
        raise HTTPException(status_code=400, detail="No RFP file uploaded for this project yet")
    job_id = job_store.create()
    background_tasks.add_task(_run_parse_project, project_id, job_id)
    return {"job_id": job_id, "status": JobStatus.PENDING}


@router.get("/parse-status/{job_id}")
async def get_project_parse_status(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "status": job["status"], "result": job.get("result"), "error": job.get("error")}


# ── Run analysis from project files ──────────────────────────────────────────

@router.post("/{project_id}/analyze")
async def analyze_project(project_id: str, background_tasks: BackgroundTasks):
    """Run analysis using stored project files. No re-upload needed."""
    from app.api.routes.analysis import _run_analysis_job
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not get_rfp_path(project_id):
        raise HTTPException(status_code=400, detail="No RFP file in project")
    if not get_supplier_paths(project_id):
        raise HTTPException(status_code=400, detail="No supplier files in project")
    job_id = job_store.create()
    # Pass project_id as rfp_id so existing analysis logic resolves files from project store
    background_tasks.add_task(_run_analysis_job, project_id, job_id, project_id)
    return {"job_id": job_id, "status": JobStatus.PENDING}
