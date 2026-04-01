"""
suppliers.py  v4.0  — DB-backed

All supplier CRUD and onboarding endpoints now read/write via SQLModel.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_db
from app.models.supplier import Supplier, SupplierDocument
from app.models.comms    import CommunicationLog
from app.agents.supplier_onboarding_agent import SupplierOnboardingAgent
from app.agents.comms_agent               import CommsAgent

router = APIRouter()

UPLOAD_DIR = Path("uploads/supplier_docs")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ── Pydantic models ────────────────────────────────────────────────────────

class SupplierCreate(BaseModel):
    name:         str
    email:        str
    contact_name: Optional[str] = None
    phone:        Optional[str] = None
    category:     Optional[str] = None

class BulkInviteRequest(BaseModel):
    supplier_ids: List[str]
    project_id:   str
    rfp_title:    Optional[str] = ""


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post("/")
def create_supplier(payload: SupplierCreate, db: Session = Depends(get_db)):
    existing = db.exec(select(Supplier).where(Supplier.email == payload.email)).first()
    if existing:
        raise HTTPException(409, detail="Supplier with this email already exists.")
    supplier = Supplier(**payload.model_dump())
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier.model_dump()


@router.get("/")
def list_suppliers(
    category: Optional[str] = None,
    status:   Optional[str] = None,
    db:       Session       = Depends(get_db),
):
    stmt = select(Supplier)
    if category:
        stmt = stmt.where(Supplier.category == category)
    if status:
        stmt = stmt.where(Supplier.status == status)
    suppliers = db.exec(stmt.order_by(Supplier.name)).all()
    return {"suppliers": [s.model_dump() for s in suppliers], "total": len(suppliers)}


@router.get("/{supplier_id}")
def get_supplier(supplier_id: str, db: Session = Depends(get_db)):
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(404, detail="Supplier not found.")
    docs = db.exec(select(SupplierDocument).where(SupplierDocument.supplier_id == supplier_id)).all()
    return {**supplier.model_dump(), "documents": [d.model_dump() for d in docs]}


@router.post("/{supplier_id}/invite")
def invite_supplier(
    supplier_id: str,
    project_id:  str,
    rfp_title:   str = "",
    db:          Session = Depends(get_db),
):
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(404, detail="Supplier not found.")

    agent  = CommsAgent()
    result = agent.run({
        "email_type":    "rfp_invite",
        "project_id":   project_id,
        "supplier_name": supplier.name,
        "recipient":     supplier.email,
        "rfp_title":     rfp_title,
        "send":          True,
    })

    log = CommunicationLog(
        project_id=project_id,
        supplier_id=supplier.id,
        email_type="rfp_invite",
        recipient=supplier.email,
        subject=result.get("subject", "RFP Invitation"),
        body=result.get("body", ""),
        sent=result.get("sent", False),
        sent_at=datetime.utcnow() if result.get("sent") else None,
    )
    db.add(log)
    supplier.status = "invited"
    supplier.updated_at = datetime.utcnow()
    db.commit()
    return {"supplier_id": supplier_id, "invited": True, "email_result": result}


@router.post("/{supplier_id}/validate-docs")
async def validate_supplier_docs(
    supplier_id: str,
    files:       List[UploadFile] = File(...),
    db:          Session          = Depends(get_db),
):
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(404, detail="Supplier not found.")

    saved_docs = []
    for f in files:
        dest = UPLOAD_DIR / f"{supplier_id}_{uuid.uuid4().hex}_{f.filename}"
        dest.write_bytes(await f.read())
        doc_type = _infer_doc_type(f.filename)
        doc = SupplierDocument(
            supplier_id=supplier_id,
            doc_type=doc_type,
            filename=f.filename,
            file_path=str(dest),
        )
        db.add(doc)
        saved_docs.append({"filename": f.filename, "doc_type": doc_type})
    db.flush()

    agent  = SupplierOnboardingAgent()
    result = agent.run({
        "supplier_id":   supplier_id,
        "supplier_name": supplier.name,
        "recipient":     supplier.email,
        "documents":     saved_docs,
    })

    # Mark verified docs
    for doc_name in result.get("verified_docs", []):
        docs = db.exec(
            select(SupplierDocument)
            .where(SupplierDocument.supplier_id == supplier_id)
            .where(SupplierDocument.filename == doc_name)
        ).all()
        for d in docs:
            d.verified = True

    if result.get("onboarding_complete"):
        supplier.onboarding_complete = True
        supplier.status = "active"
    else:
        supplier.status = "onboarding"
    supplier.updated_at = datetime.utcnow()
    db.commit()
    return result


@router.get("/{supplier_id}/status")
def get_supplier_status(supplier_id: str, db: Session = Depends(get_db)):
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(404, detail="Supplier not found.")
    docs = db.exec(select(SupplierDocument).where(SupplierDocument.supplier_id == supplier_id)).all()
    return {
        "supplier_id":          supplier_id,
        "name":                 supplier.name,
        "status":               supplier.status,
        "onboarding_complete":  supplier.onboarding_complete,
        "documents_submitted":  len(docs),
        "documents_verified":   sum(1 for d in docs if d.verified),
    }


@router.post("/bulk-invite")
def bulk_invite(payload: BulkInviteRequest, db: Session = Depends(get_db)):
    results = []
    for sid in payload.supplier_ids:
        supplier = db.get(Supplier, sid)
        if not supplier:
            results.append({"supplier_id": sid, "error": "not found"})
            continue
        agent  = CommsAgent()
        result = agent.run({
            "email_type":    "rfp_invite",
            "project_id":   payload.project_id,
            "supplier_name": supplier.name,
            "recipient":     supplier.email,
            "rfp_title":     payload.rfp_title or "",
            "send":          True,
        })
        log = CommunicationLog(
            project_id=payload.project_id,
            supplier_id=supplier.id,
            email_type="rfp_invite",
            recipient=supplier.email,
            subject=result.get("subject", "RFP Invitation"),
            body=result.get("body", ""),
            sent=result.get("sent", False),
            sent_at=datetime.utcnow() if result.get("sent") else None,
        )
        db.add(log)
        supplier.status     = "invited"
        supplier.updated_at = datetime.utcnow()
        results.append({"supplier_id": sid, "invited": True})
    db.commit()
    return {"results": results, "total_invited": sum(1 for r in results if r.get("invited"))}


# ── helpers ────────────────────────────────────────────────────────────────

def _infer_doc_type(filename: str) -> str:
    name = filename.lower()
    if any(k in name for k in ("reg", "cert", "incorp")): return "registration"
    if any(k in name for k in ("tax", "gst", "vat")):     return "tax_cert"
    if any(k in name for k in ("insur",)):                return "insurance"
    if any(k in name for k in ("bank", "guarantee")):     return "bank_guarantee"
    if any(k in name for k in ("iso", "quality")):        return "quality_cert"
    return "other"
