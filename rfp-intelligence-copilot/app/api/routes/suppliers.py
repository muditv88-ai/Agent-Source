"""
suppliers.py  NEW — v3.0

Supplier directory management + agentic onboarding flow.

Endpoints:
  GET  /suppliers                          — list all suppliers
  POST /suppliers                          — create / register a supplier
  GET  /suppliers/{supplier_id}            — get supplier details
  POST /suppliers/{supplier_id}/invite     — send onboarding invite via SupplierOnboardingAgent
  POST /suppliers/{supplier_id}/validate   — validate submitted onboarding docs
  GET  /suppliers/{supplier_id}/status     — get onboarding status + completeness
  POST /suppliers/bulk-invite              — send invites to multiple suppliers for a project
"""
from typing import Any, Dict, List, Optional
from datetime import datetime
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr

from app.agents.supplier_onboarding_agent import SupplierOnboardingAgent
from app.services.document_parser import extract_text

router = APIRouter()

# ── In-memory store (replace with DB in production) ───────────────────────
_SUPPLIERS: Dict[str, Dict] = {}


# ── Request / response models ─────────────────────────────────────────────

class SupplierCreateRequest(BaseModel):
    name:    str
    email:   str
    contact: Optional[str] = None
    country: Optional[str] = None
    category: Optional[str] = None   # e.g. "Manufacturing", "IT Services"
    notes:   Optional[str] = None

class OnboardingInviteRequest(BaseModel):
    project_id:   str
    project_name: Optional[str] = None
    portal_link:  Optional[str] = "https://sourceiq.app/onboard"

class BulkInviteRequest(BaseModel):
    supplier_ids: List[str]
    project_id:   str
    project_name: Optional[str] = None
    portal_link:  Optional[str] = "https://sourceiq.app/onboard"


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("")
async def list_suppliers(
    category: Optional[str] = None,
    status:   Optional[str] = None,
    limit:    int = 100,
):
    """List all registered suppliers with optional filters."""
    suppliers = list(_SUPPLIERS.values())
    if category:
        suppliers = [s for s in suppliers if s.get("category") == category]
    if status:
        suppliers = [s for s in suppliers if s.get("onboarding_status") == status]
    return {
        "suppliers": suppliers[:limit],
        "total": len(suppliers),
    }


@router.post("")
async def create_supplier(payload: SupplierCreateRequest):
    """Register a new supplier in the directory."""
    supplier_id = str(uuid.uuid4())
    supplier = {
        "supplier_id":        supplier_id,
        "name":               payload.name,
        "email":              payload.email,
        "contact":            payload.contact,
        "country":            payload.country,
        "category":           payload.category,
        "notes":              payload.notes,
        "onboarding_status":  "not_started",
        "created_at":         datetime.utcnow().isoformat(),
    }
    _SUPPLIERS[supplier_id] = supplier
    return {"status": "created", "supplier": supplier}


@router.get("/{supplier_id}")
async def get_supplier(supplier_id: str):
    """Get supplier profile and onboarding status."""
    supplier = _SUPPLIERS.get(supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier


@router.post("/{supplier_id}/invite")
async def send_onboarding_invite(
    supplier_id: str,
    payload: OnboardingInviteRequest,
):
    """
    Send an onboarding invitation email to the supplier.
    Uses SupplierOnboardingAgent which drafts via CommsAgent LLM.
    """
    supplier = _SUPPLIERS.get(supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    agent = SupplierOnboardingAgent()
    try:
        result = agent.run({
            "step":           "invite",
            "supplier_email": supplier["email"],
            "supplier_name":  supplier["name"],
            "project_id":     payload.project_id,
            "portal_link":    payload.portal_link,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    _SUPPLIERS[supplier_id]["onboarding_status"] = "invited"
    _SUPPLIERS[supplier_id]["invited_at"] = datetime.utcnow().isoformat()
    return {"status": "invited", "result": result, "supplier_id": supplier_id}


@router.post("/{supplier_id}/validate")
async def validate_supplier_docs(
    supplier_id: str,
    files: List[UploadFile] = File(...),
):
    """
    Validate uploaded onboarding documents against the checklist.
    SupplierOnboardingAgent checks completeness and auto-requests missing docs.
    """
    supplier = _SUPPLIERS.get(supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    parsed_docs = []
    for f in files:
        content = await f.read()
        try:
            text = extract_text(content, f.filename)
        except Exception:
            text = ""
        parsed_docs.append({
            "doc_type":  _infer_doc_type(f.filename),
            "filename":  f.filename,
            "text_len":  len(text),
        })

    agent = SupplierOnboardingAgent()
    try:
        result = agent.run({
            "step":          "validate",
            "supplier_id":   supplier_id,
            "uploaded_docs": parsed_docs,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    _SUPPLIERS[supplier_id]["onboarding_status"] = result.get("status", "pending_docs")
    _SUPPLIERS[supplier_id]["completeness_score"] = result.get("completeness_score", 0)
    return result


@router.get("/{supplier_id}/status")
async def get_supplier_onboarding_status(supplier_id: str):
    """Return current onboarding status and completeness score."""
    supplier = _SUPPLIERS.get(supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return {
        "supplier_id":        supplier_id,
        "name":               supplier["name"],
        "onboarding_status":  supplier.get("onboarding_status", "not_started"),
        "completeness_score": supplier.get("completeness_score", 0),
        "invited_at":         supplier.get("invited_at"),
    }


@router.post("/bulk-invite")
async def bulk_invite_suppliers(payload: BulkInviteRequest):
    """
    Send onboarding invitations to multiple suppliers at once.
    Each invite is processed individually via SupplierOnboardingAgent.
    """
    results = {}
    agent = SupplierOnboardingAgent()
    for sid in payload.supplier_ids:
        supplier = _SUPPLIERS.get(sid)
        if not supplier:
            results[sid] = {"error": "Supplier not found"}
            continue
        try:
            r = agent.run({
                "step":           "invite",
                "supplier_email": supplier["email"],
                "supplier_name":  supplier["name"],
                "project_id":     payload.project_id,
                "portal_link":    payload.portal_link,
            })
            results[sid] = {"status": "invited", "result": r}
            _SUPPLIERS[sid]["onboarding_status"] = "invited"
        except Exception as e:
            results[sid] = {"error": str(e)}
    return {
        "project_id": payload.project_id,
        "results": results,
        "invited_count": sum(1 for r in results.values() if r.get("status") == "invited"),
    }


# ── Helpers ────────────────────────────────────────────────────────────────

def _infer_doc_type(filename: str) -> str:
    """Guess document type from filename keywords."""
    fn = filename.lower()
    if any(k in fn for k in ["reg", "incorporat", "certificate"]):
        return "company_registration"
    if any(k in fn for k in ["tax", "gst", "vat", "pan"]):
        return "tax_id"
    if any(k in fn for k in ["bank", "account"]):
        return "bank_details"
    if any(k in fn for k in ["iso", "certif"]):
        return "iso_certification"
    if any(k in fn for k in ["insur"]):
        return "insurance_certificate"
    if any(k in fn for k in ["contact", "address"]):
        return "contact_details"
    return "other"
