import uuid
import shutil
import json
import asyncio
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Form
from typing import Optional
from app.models.schemas import UploadResponse, ParseResponse, RFPQuestion
from app.services.document_parser import parse_document
from app.services.rfp_extractor import extract_rfp_questions
from app.services.job_store import job_store, JobStatus

router = APIRouter()

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
META_DIR = Path("metadata")
META_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".pdf", ".docx"}

_executor = ThreadPoolExecutor(max_workers=10)


# ─── Upload (fast — just saves file) ────────────────────────────────────────

@router.post("/upload", response_model=UploadResponse)
async def upload_rfp(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )
    rfp_id = str(uuid.uuid4())
    dest_path = UPLOAD_DIR / f"{rfp_id}_rfp{suffix}"
    with dest_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return UploadResponse(rfp_id=rfp_id, filename=file.filename, status="uploaded")


# ─── Parse: async background job ─────────────────────────────────────────

def _do_parse_sync(rfp_id: str) -> dict:
    upload_files = list(UPLOAD_DIR.glob(f"{rfp_id}_rfp*"))
    if not upload_files:
        raise FileNotFoundError(f"RFP file not found for id {rfp_id}")

    parsed_doc = parse_document(str(upload_files[0]))
    full_text = parsed_doc.get("full_text", "")
    print(f"[parse] document text length: {len(full_text)} chars")

    extracted = extract_rfp_questions(full_text)
    raw_questions = extracted.get("questions", [])
    print(f"[parse] extracted {len(raw_questions)} questions, categories: {extracted.get('categories', [])}")

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

    meta_path = META_DIR / f"{rfp_id}_questions.json"
    meta_path.write_text(json.dumps([q.dict() for q in questions]))
    print(f"[parse] saved {len(questions)} questions to {meta_path}")

    return {
        "rfp_id": rfp_id,
        "status": "parsed",
        "questions": [q.dict() for q in questions],
        "categories": extracted.get("categories", []),
        "total_questions": len(questions),
    }


async def _run_parse(rfp_id: str, job_id: str):
    job_store.set_running(job_id)
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(_executor, _do_parse_sync, rfp_id)
        job_store.set_completed(job_id, result)
    except Exception as e:
        error_detail = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        print(f"[parse ERROR] job {job_id}: {error_detail}")
        job_store.set_failed(job_id, str(e))


@router.post("/{rfp_id}/parse")
async def parse_rfp(rfp_id: str, background_tasks: BackgroundTasks):
    job_id = job_store.create()
    background_tasks.add_task(_run_parse, rfp_id, job_id)
    return {"job_id": job_id, "status": JobStatus.PENDING}


@router.get("/parse-status/{job_id}")
async def get_parse_status(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job_id,
        "status": job["status"],
        "result": job.get("result"),
        "error": job.get("error"),
    }


# ─── Supplier upload ─────────────────────────────────────────────────────────────────

@router.post("/{rfp_id}/supplier", response_model=dict)
async def upload_supplier_response(
    rfp_id: str,
    file: UploadFile = File(...),
    supplier_name: Optional[str] = Form(None),
):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    supplier_id = str(uuid.uuid4())
    dest_path = UPLOAD_DIR / f"{rfp_id}_supplier_{supplier_id}{suffix}"
    with dest_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # ── Derive supplier name: form field > original filename stem > uuid
    if not supplier_name or not supplier_name.strip():
        # Use original filename without extension as fallback
        supplier_name = Path(file.filename).stem.strip() or supplier_id

    # Persist supplier name mapping so pricing job can look it up
    suppliers_meta_path = META_DIR / f"{rfp_id}_suppliers.json"
    if suppliers_meta_path.exists():
        suppliers_meta = json.loads(suppliers_meta_path.read_text())
    else:
        suppliers_meta = {}
    suppliers_meta[str(dest_path)] = supplier_name
    suppliers_meta_path.write_text(json.dumps(suppliers_meta))

    return {"rfp_id": rfp_id, "supplier_id": supplier_id, "supplier_name": supplier_name, "status": "uploaded"}
