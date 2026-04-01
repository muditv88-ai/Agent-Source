"""
communications.py  v4.0  — DB-backed

All sent emails are persisted to CommunicationLog.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_db
from app.models.comms import CommunicationLog
from app.agents.comms_agent import CommsAgent

router = APIRouter()

EMAIL_TYPES = [
    "rfp_invite", "deadline_reminder", "clarification",
    "award", "regret", "onboarding",
]


# ── Pydantic models ────────────────────────────────────────────────────────

class DraftRequest(BaseModel):
    email_type:    str
    project_id:    Optional[str] = None
    supplier_id:   Optional[str] = None
    supplier_name: Optional[str] = None
    recipient:     str
    context:       Optional[dict] = {}

class SendRequest(DraftRequest):
    pass   # same fields; send=True is set internally


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post("/draft")
def draft_email(payload: DraftRequest, db: Session = Depends(get_db)):
    """Generate an email draft without sending. Saved to DB with sent=False."""
    _validate_type(payload.email_type)
    agent  = CommsAgent()
    result = agent.run({
        "email_type":    payload.email_type,
        "project_id":   payload.project_id,
        "supplier_name": payload.supplier_name or "",
        "recipient":     payload.recipient,
        "send":          False,
        **(payload.context or {}),
    })
    log = CommunicationLog(
        project_id=payload.project_id,
        supplier_id=payload.supplier_id,
        email_type=payload.email_type,
        recipient=payload.recipient,
        subject=result.get("subject", ""),
        body=result.get("body", ""),
        sent=False,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return {"log_id": log.id, **result}


@router.post("/send")
def send_email(payload: SendRequest, db: Session = Depends(get_db)):
    """Generate and send an email. Persists to DB with sent=True (or False on failure)."""
    _validate_type(payload.email_type)
    agent  = CommsAgent()
    result = agent.run({
        "email_type":    payload.email_type,
        "project_id":   payload.project_id,
        "supplier_name": payload.supplier_name or "",
        "recipient":     payload.recipient,
        "send":          True,
        **(payload.context or {}),
    })
    sent = result.get("sent", False)
    log  = CommunicationLog(
        project_id=payload.project_id,
        supplier_id=payload.supplier_id,
        email_type=payload.email_type,
        recipient=payload.recipient,
        subject=result.get("subject", ""),
        body=result.get("body", ""),
        sent=sent,
        sent_at=datetime.utcnow() if sent else None,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return {"log_id": log.id, **result}


@router.get("/history/{project_id}")
def get_history(
    project_id: str,
    email_type: Optional[str] = None,
    db:         Session       = Depends(get_db),
):
    """Return full communication history for a project, optionally filtered by type."""
    stmt = select(CommunicationLog).where(CommunicationLog.project_id == project_id)
    if email_type:
        stmt = stmt.where(CommunicationLog.email_type == email_type)
    logs = db.exec(stmt.order_by(CommunicationLog.created_at.desc())).all()
    return {"project_id": project_id, "logs": [l.model_dump() for l in logs], "total": len(logs)}


@router.get("/templates")
def list_templates():
    """Return all supported email types."""
    return {"email_types": EMAIL_TYPES}


# ── helpers ────────────────────────────────────────────────────────────────

def _validate_type(email_type: str) -> None:
    if email_type not in EMAIL_TYPES:
        raise HTTPException(
            422,
            detail=f"Invalid email_type '{email_type}'. Valid: {EMAIL_TYPES}",
        )
