import uuid
import shutil
import json
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.models.schemas import UploadResponse, ParseResponse, RFPQuestion
from app.services.document_parser import parse_document
from app.services.rfp_extractor import extract_rfp_questions

router = APIRouter()

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
META_DIR = Path("metadata")
META_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".pdf", ".docx"}


@router.post("/upload", response_model=UploadResponse)
async def upload_rfp(file: UploadFile = File(...)):
    """Upload any RFP document type"""
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


@router.post("/{rfp_id}/parse", response_model=ParseResponse)
async def parse_rfp(rfp_id: str):
    """Parse uploaded RFP and extract structured questions using AI"""
    upload_files = list(UPLOAD_DIR.glob(f"{rfp_id}_rfp*"))
    if not upload_files:
        raise HTTPException(status_code=404, detail="RFP file not found")

    parsed_doc = parse_document(str(upload_files[0]))
    extracted = extract_rfp_questions(parsed_doc["full_text"])

    questions = [
        RFPQuestion(
            question_id=q["question_id"],
            category=q["category"],
            question_text=q["question_text"],
            question_type=q.get("question_type", "qualitative"),
            weight=float(q.get("weight", 10)),
            scoring_guidance=q.get("scoring_guidance"),
        )
        for q in extracted.get("questions", [])
    ]

    # Save extracted questions for use in analysis
    meta_path = META_DIR / f"{rfp_id}_questions.json"
    meta_path.write_text(json.dumps([q.dict() for q in questions]))

    return ParseResponse(
        rfp_id=rfp_id,
        status="parsed",
        questions=questions,
        categories=extracted.get("categories", []),
        total_questions=len(questions),
    )


@router.post("/{rfp_id}/supplier", response_model=dict)
async def upload_supplier_response(rfp_id: str, file: UploadFile = File(...)):
    """Upload a supplier response document"""
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    supplier_id = str(uuid.uuid4())
    dest_path = UPLOAD_DIR / f"{rfp_id}_supplier_{supplier_id}{suffix}"

    with dest_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {"rfp_id": rfp_id, "supplier_id": supplier_id, "status": "uploaded"}
