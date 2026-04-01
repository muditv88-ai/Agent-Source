"""
RFP and RFPQuestion SQLModel table definitions.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


def _uuid() -> str:
    return str(uuid.uuid4())


class RFP(SQLModel, table=True):
    __tablename__ = "rfp"

    id:           str      = Field(default_factory=_uuid, primary_key=True)
    project_id:   str      = Field(index=True)
    title:        str
    category:     str
    scope:        str
    status:       str      = Field(default="draft")   # draft | published | closed | awarded
    submission_deadline: Optional[datetime] = None
    created_at:   datetime = Field(default_factory=datetime.utcnow)
    updated_at:   datetime = Field(default_factory=datetime.utcnow)

    # relationships
    questions: List["RFPQuestion"] = Relationship(back_populates="rfp")
    responses: List["BidResponse"] = Relationship(back_populates="rfp")   # type: ignore[name-defined]
    drawings:  List["Drawing"]     = Relationship(back_populates="rfp")   # type: ignore[name-defined]


class RFPQuestion(SQLModel, table=True):
    __tablename__ = "rfp_question"

    id:         str   = Field(default_factory=_uuid, primary_key=True)
    rfp_id:     str   = Field(foreign_key="rfp.id", index=True)
    section:    str
    question:   str
    weight:     float = Field(default=0.0)   # 0-100, used by TechnicalAnalysisAgent
    required:   bool  = Field(default=True)
    order:      int   = Field(default=0)

    rfp: Optional[RFP] = Relationship(back_populates="questions")
