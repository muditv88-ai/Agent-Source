"""
schemas/communications.py

Pydantic models for /communications endpoints.
"""
from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, EmailStr


class DraftRequest(BaseModel):
    type: str                          # rfp_invite | deadline_reminder | clarification_request | etc.
    project_id: Optional[str] = None
    supplier_id: Optional[str] = None
    params: Dict[str, Any] = {}        # Template variables (supplier_name, deadline, ...)


class SendRequest(DraftRequest):
    recipient_email: EmailStr
    auto_send: bool = True


class CommLogEntry(BaseModel):
    project_id: str
    supplier_id: Optional[str] = None
    type: str
    subject: str
    sent_at: str
    sent: bool = False


class TemplateInfo(BaseModel):
    type: str
    description: str
    required_params: list[str]
