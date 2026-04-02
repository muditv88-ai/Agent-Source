"""
rfp.py  v4.2  — adds push_log instrumentation for live dashboard

Changes from v4.1:
  - push_log calls added to /rfp/generate, /rfp/extract,
    /rfp/upload-supplier-response so the dashboard ticker shows
    real RFP Generation Agent + Response Intake Agent activity.
"""
from __future__ import annotations

import json as _json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_db
from app.models.rfp import RFP, RFPQuestion
from app.models.bid import BidResponse

from app.services.rfp_extractor   import extract_rfp_questions
from app.services.document_parser import extract_text
from app.services.supplier_parser import parse_supplier_response

from app.agents.rfp_generation_agent  import RFPGenerationAgent
from app.agents.response_intake_agent import ResponseIntakeAgent

from app.api.routes.agent_logs import push_log

# GCS — imported lazily so the app still boots if google-cloud-storage is missing
try:
    from app.services import gcs_storage as _gcs
    _GCS_ENABLED = True
except Exception:
    _GCS_ENABLED = False

router = APIRouter()


# ── helpers ───────────────────────────────────────────────────────────────

def _save_to_gcs(
    project_id: Optional[str],
    category: str,
    filename: str,
    file_bytes: bytes,
    content_type: str,
) -> Optional[str]:
    if not _GCS_ENABLED:
        return None
    try:
        return _gcs.upload_file(
            project_id=project_id or "unassigned",
            category=category,
            filename=filename,
            file_bytes=file_bytes,
            content_type=content_type or "application/octet-stream",
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("GCS upload failed: %s", exc)
        return None


# ── Pydantic request models ────────────────────────────────────────────────

class RFPGenerateRequest(BaseModel):
    category:     str
    scope:        str
    requirements: Optional[str] = ""
    project_id:   Optional[str] = None
    title:        Optional[str] = None
    deadline:     Optional[str] = None

class WeightUpdateRequest(BaseModel):
    weights: dict


# ════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════

@router.post("/extract")
async def extract_rfp(
    file:       UploadFile       = File(...),
    project_id: Optional[str]   = Form(None),
):
    push_log(agent_id="rfp", status="running",
             message=f"Extracting RFP from {file.filename}")
    file_bytes = await file.read()

    gcs_blob = _save_to_gcs(
        project_id=project_id,
        category="rfp_templates",
        filename=file.filename,
        file_bytes=file_bytes,
        content_type=file.content_type,
    )

    try:
        text = extract_text(file_bytes, file.filename)
    except Exception as e:
        push_log(agent_id="rfp", status="error",
                 message=f"Failed to parse {file.filename}: {e}")
        raise HTTPException(422, detail=f"Failed to parse file: {e}")
    if not text or not text.strip():
        push_log(agent_id="rfp", status="error",
                 message="No text content found in uploaded file")
        raise HTTPException(422, detail="No text content found in uploaded file.")
    try:
        result = extract_rfp_questions(text)
    except Exception as e:
        push_log(agent_id="rfp", status="error",
                 message=f"RFP extraction failed: {e}")
        raise HTTPException(500, detail=f"RFP extraction failed: {e}")

    q_count = len(result.get("questions", []))
    push_log(agent_id="rfp", status="complete",
             message=f"Extracted {q_count} RFP questions from {file.filename}",
             confidence=88)

    result["project_id"]  = project_id
    result["source_file"] = file.filename
    result["gcs_blob"]    = gcs_blob
    return result


@router.post("/parse-supplier-response")
async def parse_supplier(
    file:       UploadFile     = File(...),
    questions:  str            = Form(...),
    project_id: Optional[str] = Form(None),
):
    push_log(agent_id="rfp", status="running",
             message=f"Parsing supplier response: {file.filename}")
    file_bytes = await file.read()

    gcs_blob = _save_to_gcs(
        project_id=project_id,
        category="supplier_responses",
        filename=file.filename,
        file_bytes=file_bytes,
        content_type=file.content_type,
    )

    try:
        text          = extract_text(file_bytes, file.filename)
        rfp_questions = _json.loads(questions)
    except Exception as e:
        push_log(agent_id="rfp", status="error", message=str(e))
        raise HTTPException(422, detail=str(e))

    result = parse_supplier_response(text, rfp_questions)
    push_log(agent_id="rfp", status="complete",
             message=f"Parsed supplier response: {file.filename}",
             confidence=85)
    result["project_id"]  = project_id
    result["source_file"] = file.filename
    result["gcs_blob"]    = gcs_blob
    return result


@router.post("/generate")
async def generate_rfp(
    payload: RFPGenerateRequest,
    db:      Session = Depends(get_db),
):
    push_log(agent_id="rfp", status="running",
             message=f"Generating RFP for {payload.category} — {payload.scope[:60]}")
    agent = RFPGenerationAgent()
    import time as _time
    t0 = _time.time()
    try:
        result = agent.run({
            "mode":         "generate",
            "category":     payload.category,
            "scope":        payload.scope,
            "requirements": payload.requirements or "",
        })
    except Exception as e:
        push_log(agent_id="rfp", status="error",
                 message=f"RFP generation failed: {e}")
        raise HTTPException(500, detail=str(e))

    duration_ms = int((_time.time() - t0) * 1000)

    rfp = RFP(
        project_id=payload.project_id or result.get("project_id", ""),
        title=payload.title or f"{payload.category} RFP",
        category=payload.category,
        scope=payload.scope,
        status="draft",
        submission_deadline=datetime.fromisoformat(payload.deadline) if payload.deadline else None,
    )
    db.add(rfp)
    db.flush()

    questions: list = result.get("questions", [])
    for i, q in enumerate(questions):
        db.add(RFPQuestion(
            rfp_id=rfp.id,
            section=q.get("section", "General"),
            question=q.get("question", q) if isinstance(q, dict) else str(q),
            weight=0.0,
            required=True,
            order=i,
        ))
    db.commit()
    db.refresh(rfp)

    push_log(agent_id="rfp", status="complete",
             message=f"Drafted {len(questions)} RFP sections for {payload.category}",
             confidence=92,
             duration_ms=duration_ms)

    result["rfp_id"]     = rfp.id
    result["project_id"] = rfp.project_id
    return result


@router.get("/list")
def list_rfps(db: Session = Depends(get_db)):
    rfps = db.exec(select(RFP).order_by(RFP.created_at.desc())).all()
    return {"rfps": [r.model_dump() for r in rfps], "total": len(rfps)}


@router.get("/{rfp_id}")
def get_rfp(rfp_id: str, db: Session = Depends(get_db)):
    rfp = db.get(RFP, rfp_id)
    if not rfp:
        raise HTTPException(404, detail="RFP not found")
    questions = db.exec(select(RFPQuestion).where(RFPQuestion.rfp_id == rfp_id).order_by(RFPQuestion.order)).all()
    return {**rfp.model_dump(), "questions": [q.model_dump() for q in questions]}


@router.post("/upload-supplier-response")
async def upload_supplier_response(
    file:        UploadFile       = File(...),
    project_id:  str              = Form(...),
    questions:   str              = Form(...),
    rfp_id:      Optional[str]   = Form(None),
    supplier_id: Optional[str]   = Form(None),
    db:          Session          = Depends(get_db),
):
    push_log(agent_id="rfp", status="running",
             message=f"Intake: processing supplier response {file.filename}")
    file_bytes = await file.read()

    gcs_blob = _save_to_gcs(
        project_id=project_id,
        category="supplier_responses",
        filename=file.filename,
        file_bytes=file_bytes,
        content_type=file.content_type,
    )

    try:
        text          = extract_text(file_bytes, file.filename)
        rfp_questions = _json.loads(questions)
    except Exception as e:
        push_log(agent_id="rfp", status="error", message=str(e))
        raise HTTPException(422, detail=str(e))

    agent = ResponseIntakeAgent()
    try:
        result = agent.run({
            "file_text":     text,
            "rfp_questions": rfp_questions,
            "project_id":    project_id,
        })
    except Exception as e:
        push_log(agent_id="rfp", status="error",
                 message=f"Response intake failed: {e}")
        raise HTTPException(500, detail=str(e))

    if rfp_id and supplier_id:
        bid = BidResponse(
            rfp_id=rfp_id,
            supplier_id=supplier_id,
            source_file=file.filename,
            completeness_pct=result.get("completeness_pct", 0.0),
            status="received",
        )
        db.add(bid)
        db.commit()
        db.refresh(bid)
        result["bid_response_id"] = bid.id

    pct = result.get("completeness_pct", 0)
    push_log(agent_id="rfp", status="complete",
             message=f"Supplier response ingested — {pct:.0f}% completeness",
             confidence=int(min(pct, 100)))

    result["source_file"] = file.filename
    result["gcs_blob"]    = gcs_blob
    return result


@router.post("/{rfp_id}/questions/weights")
def update_question_weights(
    rfp_id:  str,
    payload: WeightUpdateRequest,
    db:      Session = Depends(get_db),
):
    total = sum(payload.weights.values())
    if abs(total - 100.0) > 0.5:
        raise HTTPException(422, detail=f"Weights must sum to 100. Got {total:.1f}.")

    questions = db.exec(select(RFPQuestion).where(RFPQuestion.rfp_id == rfp_id)).all()
    if not questions:
        raise HTTPException(404, detail="No questions found for this RFP.")

    for q in questions:
        if q.section in payload.weights:
            q.weight = payload.weights[q.section]
    db.commit()
    return {"rfp_id": rfp_id, "weights": payload.weights, "status": "updated"}


@router.get("/{project_id}/completeness")
def get_response_completeness(project_id: str, db: Session = Depends(get_db)):
    rfps = db.exec(select(RFP).where(RFP.project_id == project_id)).all()
    rfp_ids = [r.id for r in rfps]

    if not rfp_ids:
        return {"project_id": project_id, "suppliers": [], "note": "No RFPs found for this project."}

    bids = db.exec(
        select(BidResponse).where(BidResponse.rfp_id.in_(rfp_ids))
    ).all()

    suppliers = [
        {
            "bid_response_id":  b.id,
            "supplier_id":      b.supplier_id,
            "rfp_id":           b.rfp_id,
            "completeness_pct": b.completeness_pct,
            "status":           b.status,
            "submitted_at":     b.submitted_at.isoformat(),
        }
        for b in bids
    ]
    return {"project_id": project_id, "suppliers": suppliers, "total": len(suppliers)}
