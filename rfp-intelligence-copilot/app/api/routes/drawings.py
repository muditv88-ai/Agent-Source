"""
drawings.py  NEW — v3.0

Technical drawing management for RFP line items.
Supports upload, attachment to line items, listing, and deletion.

Endpoints:
  POST /drawings/upload                  — upload a drawing file (stores to local /uploads)
  POST /drawings/attach                  — attach an uploaded drawing to a line item
  GET  /drawings/{project_id}            — list all drawings for a project
  GET  /drawings/{project_id}/{item_id}  — get drawings for a specific line item
  DELETE /drawings/{drawing_id}          — remove a drawing record
"""
from typing import Dict, List, Optional
from datetime import datetime
import os
import uuid
import shutil

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.agents.rfp_generation_agent import RFPGenerationAgent

router = APIRouter()

UPLOAD_DIR = os.environ.get("DRAWING_UPLOAD_DIR", "uploads/drawings")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ── In-memory drawing registry (replace with DB in production) ─────────────
_DRAWINGS: Dict[str, Dict] = {}


# ── Request models ─────────────────────────────────────────────────────────

class DrawingAttachRequest(BaseModel):
    drawing_id:  str
    line_item_id: str
    part_number:  str
    revision:     Optional[str] = "A"
    notes:        Optional[str] = None


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_drawing(
    file:        UploadFile = File(...),
    project_id:  str        = Form(...),
    part_number: str        = Form(...),
    revision:    str        = Form("A"),
    description: Optional[str] = Form(None),
):
    """
    Upload a technical drawing file (PDF, DWG, DXF, PNG, JPG).
    Stores to DRAWING_UPLOAD_DIR and registers in drawing registry.
    """
    allowed = {".pdf", ".dwg", ".dxf", ".png", ".jpg", ".jpeg", ".svg", ".tif", ".tiff"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"File type '{ext}' not allowed. Supported: {', '.join(sorted(allowed))}"
        )

    drawing_id = str(uuid.uuid4())
    safe_name  = f"{drawing_id}{ext}"
    dest_path  = os.path.join(UPLOAD_DIR, safe_name)

    contents = await file.read()
    with open(dest_path, "wb") as f:
        f.write(contents)

    drawing_url = f"/static/drawings/{safe_name}"  # or S3 URL when storage is wired

    record = {
        "drawing_id":   drawing_id,
        "project_id":   project_id,
        "filename":     file.filename,
        "stored_path":  dest_path,
        "drawing_url":  drawing_url,
        "part_number":  part_number,
        "revision":     revision,
        "description":  description,
        "line_item_id": None,   # attached separately
        "uploaded_at":  datetime.utcnow().isoformat(),
    }
    _DRAWINGS[drawing_id] = record

    return {
        "status":     "uploaded",
        "drawing_id": drawing_id,
        "drawing_url": drawing_url,
        "filename":   file.filename,
    }


@router.post("/attach")
async def attach_drawing(payload: DrawingAttachRequest):
    """
    Attach an uploaded drawing to an RFP line item.
    Uses RFPGenerationAgent.attach_drawing() to register the association.
    """
    drawing = _DRAWINGS.get(payload.drawing_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="Drawing not found. Upload first.")

    agent = RFPGenerationAgent()
    try:
        result = agent._attach_drawing(
            line_item_id=payload.line_item_id,
            drawing_url=drawing["drawing_url"],
            part_number=payload.part_number,
            revision=payload.revision or "A",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    _DRAWINGS[payload.drawing_id]["line_item_id"] = payload.line_item_id
    _DRAWINGS[payload.drawing_id]["attached_at"]  = datetime.utcnow().isoformat()
    if payload.notes:
        _DRAWINGS[payload.drawing_id]["notes"] = payload.notes

    return {
        "status":      "attached",
        "drawing_id":  payload.drawing_id,
        "line_item_id": payload.line_item_id,
        "result":      result,
    }


@router.get("/{project_id}")
async def list_drawings(project_id: str):
    """List all drawings uploaded for a project."""
    drawings = [
        d for d in _DRAWINGS.values()
        if d["project_id"] == project_id
    ]
    return {
        "project_id": project_id,
        "drawings":   drawings,
        "count":      len(drawings),
    }


@router.get("/{project_id}/{line_item_id}")
async def get_drawings_for_line_item(project_id: str, line_item_id: str):
    """Get all drawings attached to a specific line item."""
    drawings = [
        d for d in _DRAWINGS.values()
        if d["project_id"] == project_id
        and d.get("line_item_id") == line_item_id
    ]
    return {
        "project_id":   project_id,
        "line_item_id": line_item_id,
        "drawings":     drawings,
        "count":        len(drawings),
    }


@router.delete("/{drawing_id}")
async def delete_drawing(drawing_id: str):
    """Remove a drawing record (and optionally the stored file)."""
    drawing = _DRAWINGS.pop(drawing_id, None)
    if not drawing:
        raise HTTPException(status_code=404, detail="Drawing not found")
    stored = drawing.get("stored_path")
    if stored and os.path.exists(stored):
        try:
            os.remove(stored)
        except OSError:
            pass
    return {"status": "deleted", "drawing_id": drawing_id}
