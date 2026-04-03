"""
RFP and RFPQuestion SQLModel table definitions.

NOTE: `from __future__ import annotations` is intentionally absent.
SQLAlchemy's relationship() resolver evaluates annotations eagerly at
mapper-configuration time.  The `from __future__` import defers ALL
annotations to strings, which breaks the generic List['RFPQuestion']
resolution and causes an InvalidRequestError on first DB query.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


def _uuid() -> str:
    return str(uuid.uuid4())


class RFPQuestion(SQLModel, table=True):
    """Defined first so RFP can reference it without a forward string."""
    __tablename__ = "rfp_question"

    id:       str   = Field(default_factory=_uuid, primary_key=True)
    rfp_id:   str   = Field(foreign_key="rfp.id", index=True)
    section:  str   = Field(default="General")
    question: str
    weight:   float = Field(default=0.0)   # 0-100, used by TechnicalAnalysisAgent
    required: bool  = Field(default=True)
    order:    int   = Field(default=0)

    # back-ref — defined after RFP class via forward ref string
    rfp: Optional["RFP"] = Relationship(back_populates="questions")


class RFP(SQLModel, table=True):
    __tablename__ = "rfp"

    id:                  str               = Field(default_factory=_uuid, primary_key=True)
    project_id:          str               = Field(index=True)
    title:               str
    category:            str
    scope:               str
    status:              str               = Field(default="draft")
    submission_deadline: Optional[datetime] = None
    created_at:          datetime          = Field(default_factory=datetime.utcnow)
    updated_at:          datetime          = Field(default_factory=datetime.utcnow)

    # Only the relationship that has a concrete target defined in this module.
    # BidResponse and Drawing back-refs were removed — they referenced models
    # not imported here and caused secondary mapper-init cascade failures.
    # RFP<->BidResponse joins are done via explicit select() queries in routes.
    questions: List[RFPQuestion] = Relationship(back_populates="rfp")
