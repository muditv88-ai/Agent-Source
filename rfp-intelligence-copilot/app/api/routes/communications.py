"""
communications.py  v3.0

Full rewrite: 475-byte stub → CommsAgent-backed endpoints.

Endpoints:
  POST /communications/draft          — LLM-draft an email (no send)
  POST /communications/send           — LLM-draft + send via SMTP
  GET  /communications/history/{pid}  — Return sent communications log for a project
  GET  /communications/templates      — List available email template types
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from app.agents.comms_agent import CommsAgent

router = APIRouter()


# ── Request models ────────────────────────────────────────────────────────

class CommsDraftRequest(BaseModel):
    type: str                             # one of EMAIL_TEMPLATES keys
    project_id: Optional[str]    = None
    supplier_id: Optional[str]   = None
    project_name: Optional[str]  = None
    supplier_name: Optional[str] = None
    deadline: Optional[str]      = None
    days_remaining: Optional[int] = None
    response_status: Optional[str] = None
    missing_questions: Optional[List[str]] = None
    missing_items: Optional[List[str]]     = None
    award_value: Optional[float]           = None
    awarded_items: Optional[List[str]]     = None
    next_steps: Optional[str]              = None
    reason: Optional[str]                  = None
    portal_link: Optional[str]             = None


class CommsSendRequest(CommsDraftRequest):
    recipient_email: str                  # required to actually send


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post("/draft")
async def draft_communication(payload: CommsDraftRequest):
    """
    Use CommsAgent to LLM-draft an email. Returns subject + body.
    Does NOT send. Use /send to send.
    """
    agent = CommsAgent()
    try:
        result = agent.run({**payload.dict(exclude_none=True), "auto_send": False})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "status": "drafted",
        "draft": result.get("drafted", {}),
    }


@router.post("/send")
async def send_communication(payload: CommsSendRequest):
    """
    LLM-draft + send email via SMTP. Also logs to communications history.
    Requires SMTP_HOST, SMTP_USER, SMTP_PASS, SMTP_FROM environment variables.
    """
    agent = CommsAgent()
    try:
        result = agent.run({**payload.dict(exclude_none=True), "auto_send": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "status": "sent" if result.get("sent") else "draft_only",
        "draft": result.get("drafted", {}),
        "send_result": result.get("send_result", {}),
    }


@router.get("/history/{project_id}")
async def get_communication_history(project_id: str, limit: int = 50):
    """
    Return sent communications log for a project.
    In full production this reads from a DB table; for now returns stub.
    """
    # TODO: replace with DB query when persistence layer is added
    return {
        "project_id": project_id,
        "communications": [],
        "note": "Persistence layer not yet wired. Connect DB in _log_comm() inside CommsAgent.",
    }


@router.get("/templates")
async def list_templates():
    """Return all available email template types."""
    from app.agents.comms_agent import EMAIL_TEMPLATES
    return {
        "templates": list(EMAIL_TEMPLATES.keys()),
        "count": len(EMAIL_TEMPLATES),
    }
