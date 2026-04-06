"""
Supplier and SupplierDocument SQLModel table definitions.

NOTE: `from __future__ import annotations` is intentionally absent.
SQLAlchemy's relationship() resolver evaluates annotations eagerly at
mapper-configuration time.  Deferred annotations break the generic List['SupplierDocument']
resolution and cause an InvalidRequestError on first DB query.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


def _uuid() -> str:
    return str(uuid.uuid4())


class Supplier(SQLModel, table=True):
    __tablename__ = "supplier"

    id:           str      = Field(default_factory=_uuid, primary_key=True)
    name:         str      = Field(index=True)
    email:        str      = Field(index=True)
    contact_name: Optional[str] = None
    phone:        Optional[str] = None
    category:     Optional[str] = None   # e.g. "IT Hardware", "Civil Works"
    status:       str      = Field(default="invited")  # invited | onboarding | active | rejected
    onboarding_complete: bool = Field(default=False)
    created_at:   datetime = Field(default_factory=datetime.utcnow)
    updated_at:   datetime = Field(default_factory=datetime.utcnow)

    # Relationships with forward references (defined after these classes)
    documents: Optional[List["SupplierDocument"]] = Relationship(back_populates="supplier")
    responses: Optional[List["BidResponse"]]      = Relationship(back_populates="supplier")   # type: ignore[name-defined]
    comms:     Optional[List["CommunicationLog"]] = Relationship(back_populates="supplier")   # type: ignore[name-defined]


class SupplierDocument(SQLModel, table=True):
    __tablename__ = "supplier_document"

    id:            str      = Field(default_factory=_uuid, primary_key=True)
    supplier_id:   str      = Field(foreign_key="supplier.id", index=True)
    doc_type:      str      # e.g. "registration", "tax_cert", "insurance", "bank_guarantee"
    filename:      str
    file_path:     str
    verified:      bool     = Field(default=False)
    uploaded_at:   datetime = Field(default_factory=datetime.utcnow)

    supplier: Optional[Supplier] = Relationship(back_populates="documents")
