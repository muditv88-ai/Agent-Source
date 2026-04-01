"""
schemas/suppliers.py

Pydantic models for /suppliers endpoints.
"""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, EmailStr, HttpUrl, field_validator


class SupplierCreate(BaseModel):
    name: str
    email: EmailStr
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    categories: List[str] = []
    portal_link: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Supplier name cannot be empty")
        return v.strip()


class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    categories: Optional[List[str]] = None
    portal_link: Optional[str] = None


class SupplierResponse(BaseModel):
    supplier_id: str
    name: str
    email: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    categories: List[str] = []
    status: str = "pending"
    onboarding_complete: bool = False
    missing_documents: List[str] = []
    created_at: Optional[str] = None


class SupplierInviteRequest(BaseModel):
    supplier_id: str
    portal_link: Optional[str] = "https://sourceiq.app/onboard"


class BulkInviteRequest(BaseModel):
    supplier_ids: List[str]
    portal_link: Optional[str] = "https://sourceiq.app/onboard"


class DocValidationResult(BaseModel):
    supplier_id: str
    validated_documents: List[str] = []
    missing_documents: List[str] = []
    onboarding_complete: bool = False
    action_taken: Optional[str] = None
