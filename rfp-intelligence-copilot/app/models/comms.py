"""
CommunicationLog SQLModel table definition.
Every email sent via CommsAgent is recorded here.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


def _uuid() -> str:
    return str(uuid.uuid4())


class CommunicationLog(SQLModel, table=True):
    __tablename__ = "communication_log"

    id:           str      = Field(default_factory=_uuid, primary_key=True)
    project_id:   Optional[str] = Field(default=None, index=True)
    supplier_id:  Optional[str] = Field(default=None, foreign_key="supplier.id", index=True)
    email_type:   str            # rfp_invite | deadline_reminder | clarification | award | regret | onboarding
    recipient:    str            # email address
    subject:      str
    body:         str
    sent:         bool     = Field(default=False)
    sent_at:      Optional[datetime] = None
    created_at:   datetime = Field(default_factory=datetime.utcnow)

    supplier: Optional["Supplier"] = Relationship(back_populates="comms")   # type: ignore[name-defined]
