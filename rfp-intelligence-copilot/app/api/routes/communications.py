"""
communications.py  — Communications Agent routes  (v2: push_log instrumentation)
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.agent_logs import push_log

router = APIRouter(tags=["Communications"])

try:
    from app.agents.communications_agent import CommunicationsAgent
    _COMMS_AGENT_AVAILABLE = True
except ImportError:
    _COMMS_AGENT_AVAILABLE = False


class DraftEmailRequest(BaseModel):
    project_id: str
    supplier_id: str
    supplier_name: str
    email_type: str = Field(
        default="clarification",
        description="One of: clarification, award, rejection, followup"
    )
    context: Optional[str] = ""
    rfp_title: Optional[str] = ""
    notes: Optional[str] = ""


class BulkOutreachRequest(BaseModel):
    project_id: str
    suppliers: List[Dict[str, Any]] = Field(
        ..., description="[{supplier_id, supplier_name, email_type, context}]"
    )
    rfp_title: Optional[str] = ""


@router.post("/draft")
async def draft_email(payload: DraftEmailRequest):
    """
    Draft a single supplier communication email.
    """
    push_log(agent_id="communications", status="running",
             message=f"Drafting {payload.email_type} email for {payload.supplier_name}")
    if not _COMMS_AGENT_AVAILABLE:
        push_log(agent_id="communications", status="error",
                 message="CommunicationsAgent not available")
        raise HTTPException(503, detail="CommunicationsAgent not available")

    try:
        t0    = time.time()
        agent = CommunicationsAgent()
        result = agent.run({
            "project_id":    payload.project_id,
            "supplier_id":   payload.supplier_id,
            "supplier_name": payload.supplier_name,
            "email_type":    payload.email_type,
            "context":       payload.context or "",
            "rfp_title":     payload.rfp_title or "",
            "notes":         payload.notes or "",
        })
        push_log(agent_id="communications", status="complete",
                 message=f"{payload.email_type.capitalize()} email drafted for {payload.supplier_name}",
                 duration_ms=int((time.time() - t0) * 1000))
        return result
    except Exception as e:
        push_log(agent_id="communications", status="error",
                 message=f"Email drafting failed: {e}")
        raise HTTPException(500, detail=str(e))


@router.post("/bulk-outreach")
async def bulk_outreach(payload: BulkOutreachRequest):
    """
    Draft communications for multiple suppliers in one call.
    """
    push_log(agent_id="communications", status="running",
             message=f"Drafting outreach for {len(payload.suppliers)} supplier(s)")
    if not _COMMS_AGENT_AVAILABLE:
        push_log(agent_id="communications", status="error",
                 message="CommunicationsAgent not available")
        raise HTTPException(503, detail="CommunicationsAgent not available")

    results = []
    errors  = []
    t0      = time.time()
    for supplier in payload.suppliers:
        try:
            agent  = CommunicationsAgent()
            result = agent.run({
                "project_id":    payload.project_id,
                "supplier_id":   supplier.get("supplier_id", ""),
                "supplier_name": supplier.get("supplier_name", "Unknown"),
                "email_type":    supplier.get("email_type", "clarification"),
                "context":       supplier.get("context", ""),
                "rfp_title":     payload.rfp_title or "",
            })
            results.append(result)
        except Exception as e:
            errors.append({"supplier": supplier.get("supplier_name"), "error": str(e)})

    push_log(agent_id="communications", status="complete",
             message=f"Drafted {len(results)}/{len(payload.suppliers)} supplier communications",
             duration_ms=int((time.time() - t0) * 1000))
    return {
        "project_id": payload.project_id,
        "drafted":    len(results),
        "errors":     errors,
        "results":    results,
    }


@router.get("/templates")
async def list_templates():
    """
    Return available communication template types.
    """
    return {
        "templates": [
            {"key": "clarification", "label": "Clarification Request"},
            {"key": "award",         "label": "Award Notification"},
            {"key": "rejection",     "label": "Rejection Notice"},
            {"key": "followup",      "label": "Follow-up / Reminder"},
        ]
    }
