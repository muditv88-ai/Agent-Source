"""
BidResponse and BidAnswer SQLModel table definitions.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


def _uuid() -> str:
    return str(uuid.uuid4())


class BidResponse(SQLModel, table=True):
    __tablename__ = "bid_response"

    id:              str      = Field(default_factory=_uuid, primary_key=True)
    rfp_id:          str      = Field(foreign_key="rfp.id", index=True)
    supplier_id:     str      = Field(foreign_key="supplier.id", index=True)
    source_file:     Optional[str] = None
    completeness_pct: float   = Field(default=0.0)   # 0-100
    total_score:     Optional[float] = None           # set after AwardAgent runs
    status:          str      = Field(default="received")  # received | clarification_sent | evaluated | awarded | rejected
    submitted_at:    datetime = Field(default_factory=datetime.utcnow)
    evaluated_at:    Optional[datetime] = None

    rfp:      Optional["RFP"]       = Relationship(back_populates="responses")   # type: ignore[name-defined]
    supplier: Optional["Supplier"]  = Relationship(back_populates="responses")   # type: ignore[name-defined]
    answers:  List["BidAnswer"]     = Relationship(back_populates="response")


class BidAnswer(SQLModel, table=True):
    __tablename__ = "bid_answer"

    id:          str   = Field(default_factory=_uuid, primary_key=True)
    response_id: str   = Field(foreign_key="bid_response.id", index=True)
    question_id: str   = Field(foreign_key="rfp_question.id", index=True)
    answer_text: Optional[str] = None
    score:       Optional[float] = None   # set by TechnicalAnalysisAgent / PricingAgent
    flagged:     bool  = Field(default=False)  # flagged for clarification

    response: Optional[BidResponse] = Relationship(back_populates="answers")
