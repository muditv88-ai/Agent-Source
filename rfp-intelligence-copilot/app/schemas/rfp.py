"""
schemas/rfp.py

Pydantic models for /rfp endpoints.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class RFPGenerateRequest(BaseModel):
    project_id: str
    category: str
    scope: str
    deadline: Optional[str] = None
    special_requirements: Optional[str] = None


class RFPGenerateResponse(BaseModel):
    rfp_id: Optional[str] = None
    project_id: str
    sections: List[Dict[str, Any]] = []
    generated_at: Optional[str] = None
    status: str = "generated"


class SupplierResponseUpload(BaseModel):
    project_id: str
    supplier_id: str
    rfp_id: Optional[str] = None


class ResponseIntakeResult(BaseModel):
    supplier_id: str
    project_id: str
    completeness_pct: float
    answered: List[str] = []
    missing: List[str] = []
    clarification_sent: bool = False
    status: str = "processed"
