"""
files.py — /files router

Persistent, per-project file storage backed by Google Cloud Storage.
Files are uploaded once and can be re-used for analysis without re-uploading.

Endpoints
---------
POST   /files/upload
    Upload a file and store its metadata in the DB + GCS.
    Form fields:
      file          — the file (multipart)
      project_id    — target project
      category      — rfp_templates | supplier_responses | drawings | misc
      display_name  — optional friendly label
      rfp_id        — optional link to an RFP record
      supplier_id   — optional link to a Supplier record

GET    /files/{project_id}
    List all stored files for a project.
    Optional query params: category, rfp_id, supplier_id

GET    /files/{project_id}/{file_id}/url
    Return a time-limited signed download URL (default 60 min).

POST   /files/{project_id}/{file_id}/analyse
    Trigger (or re-trigger) analysis on a stored file.
    Body: { "analysis_type": "rfp_parse" | "bid_intake" | "pricing" }

DELETE /files/{project_id}/{file_id}
    Delete from GCS and remove the DB record.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_db
from app.models.project_file import ProjectFile
from app.services import gcs_storage

router = APIRouter()

VALID_CATEGORIES = {"rfp_templates", "supplier_responses", "drawings", "misc"}
VALID_ANALYSIS_TYPES = {"rfp_parse", "bid_intake", "pricing"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_file_or_404(db: Session, project_id: str, file_id: str) -> ProjectFile:
    record = db.get(ProjectFile, file_id)
    if not record or record.project_id != project_id:
        raise HTTPException(status_code=404, detail="File not found")
    return record


# ---------------------------------------------------------------------------
# POST /files/upload
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=dict, status_code=201)
async def upload_project_file(
    file:         UploadFile = File(...),
    project_id:   str        = Form(...),
    category:     str        = Form(...),
    user_id:      str        = Form(...),
    display_name: Optional[str] = Form(default=None),
    rfp_id:       Optional[str] = Form(default=None),
    supplier_id:  Optional[str] = Form(default=None),
    db:           Session    = Depends(get_db),
):
    """Upload a file to GCS and register it in the project file library."""
    if category not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"category must be one of {sorted(VALID_CATEGORIES)}",
        )

    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"

    # Upload to GCS
    blob_name = gcs_storage.upload_file(
        project_id=project_id,
        category=category,
        filename=file.filename,
        file_bytes=file_bytes,
        content_type=content_type,
    )

    # Persist metadata to DB
    record = ProjectFile(
        project_id=project_id,
        user_id=user_id,
        rfp_id=rfp_id,
        supplier_id=supplier_id,
        filename=file.filename,
        display_name=display_name or file.filename,
        category=category,
        content_type=content_type,
        size_bytes=len(file_bytes),
        gcs_path=blob_name,
        analysis_status="none",
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return {
        "id":           record.id,
        "project_id":   record.project_id,
        "filename":     record.filename,
        "display_name": record.display_name,
        "category":     record.category,
        "size_bytes":   record.size_bytes,
        "gcs_path":     record.gcs_path,
        "created_at":   record.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /files/{project_id}
# ---------------------------------------------------------------------------

@router.get("/{project_id}", response_model=List[dict])
def list_project_files(
    project_id:  str,
    category:    Optional[str] = Query(default=None),
    rfp_id:      Optional[str] = Query(default=None),
    supplier_id: Optional[str] = Query(default=None),
    db:          Session = Depends(get_db),
):
    """List all files stored for a project, with optional filters."""
    stmt = select(ProjectFile).where(ProjectFile.project_id == project_id)
    if category:
        stmt = stmt.where(ProjectFile.category == category)
    if rfp_id:
        stmt = stmt.where(ProjectFile.rfp_id == rfp_id)
    if supplier_id:
        stmt = stmt.where(ProjectFile.supplier_id == supplier_id)

    records = db.exec(stmt.order_by(ProjectFile.created_at.desc())).all()

    return [
        {
            "id":              r.id,
            "filename":        r.filename,
            "display_name":    r.display_name,
            "category":        r.category,
            "content_type":    r.content_type,
            "size_bytes":      r.size_bytes,
            "rfp_id":          r.rfp_id,
            "supplier_id":     r.supplier_id,
            "analysis_status": r.analysis_status,
            "created_at":      r.created_at.isoformat(),
        }
        for r in records
    ]


# ---------------------------------------------------------------------------
# GET /files/{project_id}/{file_id}/url
# ---------------------------------------------------------------------------

@router.get("/{project_id}/{file_id}/url")
def get_download_url(
    project_id: str,
    file_id:    str,
    expiry_minutes: int = Query(default=60, ge=1, le=1440),
    db:         Session = Depends(get_db),
):
    """Return a time-limited signed URL to download the file directly from GCS."""
    record = _get_file_or_404(db, project_id, file_id)

    try:
        url = gcs_storage.get_signed_url(
            blob_name=record.gcs_path,
            expiry_minutes=expiry_minutes,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"GCS error: {exc}")

    return {
        "file_id":      record.id,
        "filename":     record.filename,
        "url":          url,
        "expires_in_minutes": expiry_minutes,
    }


# ---------------------------------------------------------------------------
# POST /files/{project_id}/{file_id}/analyse
# ---------------------------------------------------------------------------

class AnalysisRequest(BaseModel):
    analysis_type: str   # rfp_parse | bid_intake | pricing


@router.post("/{project_id}/{file_id}/analyse")
def run_analysis_on_file(
    project_id: str,
    file_id:    str,
    body:       AnalysisRequest,
    db:         Session = Depends(get_db),
):
    """
    Trigger (or re-trigger) agent analysis on a stored file.
    Downloads the file from GCS and hands bytes to the relevant agent.
    """
    if body.analysis_type not in VALID_ANALYSIS_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"analysis_type must be one of {sorted(VALID_ANALYSIS_TYPES)}",
        )

    record = _get_file_or_404(db, project_id, file_id)

    # Download bytes from GCS
    try:
        from google.cloud import storage as _gcs
        client = _gcs.Client()
        import os
        bucket = client.bucket(os.getenv("GCS_BUCKET_NAME", "rfp-copilot-files"))
        file_bytes = bucket.blob(record.gcs_path).download_as_bytes()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"GCS download error: {exc}")

    # Mark as pending
    record.analysis_status = "pending"
    record.updated_at = datetime.utcnow()
    db.add(record)
    db.commit()

    # Dispatch to the right agent
    result: dict = {}
    try:
        if body.analysis_type == "rfp_parse":
            from app.agents.rfp_generation_agent import RFPGenerationAgent
            agent = RFPGenerationAgent()
            result = agent.parse_template_bytes(file_bytes, record.filename)

        elif body.analysis_type == "bid_intake":
            from app.agents.response_intake_agent import ResponseIntakeAgent
            agent = ResponseIntakeAgent()
            result = agent.intake_from_bytes(
                file_bytes=file_bytes,
                filename=record.filename,
                rfp_id=record.rfp_id or "",
            )

        elif body.analysis_type == "pricing":
            from app.agents.pricing_analysis_agent import PricingAnalysisAgent
            agent = PricingAnalysisAgent()
            result = agent.analyse_from_bytes(
                file_bytes=file_bytes,
                filename=record.filename,
                project_id=record.project_id,
            )

        record.analysis_status = "complete"
        record.analysis_result = json.dumps(result)

    except Exception as exc:
        record.analysis_status = "error"
        record.analysis_result = json.dumps({"error": str(exc)})

    record.updated_at = datetime.utcnow()
    db.add(record)
    db.commit()
    db.refresh(record)

    return {
        "file_id":         record.id,
        "analysis_status": record.analysis_status,
        "result":          json.loads(record.analysis_result or "{}"),
    }


# ---------------------------------------------------------------------------
# DELETE /files/{project_id}/{file_id}
# ---------------------------------------------------------------------------

@router.delete("/{project_id}/{file_id}", status_code=204)
def delete_project_file(
    project_id: str,
    file_id:    str,
    db:         Session = Depends(get_db),
):
    """Delete a file from GCS and remove its DB record."""
    record = _get_file_or_404(db, project_id, file_id)
    gcs_storage.delete_file(record.gcs_path)
    db.delete(record)
    db.commit()
    return
