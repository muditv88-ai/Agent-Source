"""
Drawing SQLModel table definition.
Technical drawings attached to RFPs and line items.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


def _uuid() -> str:
    return str(uuid.uuid4())


class Drawing(SQLModel, table=True):
    __tablename__ = "drawing"

    id:           str      = Field(default_factory=_uuid, primary_key=True)
    rfp_id:       Optional[str] = Field(default=None, foreign_key="rfp.id", index=True)
    project_id:   Optional[str] = Field(default=None, index=True)
    line_item_id: Optional[str] = None   # freeform reference, no FK constraint
    filename:     str
    file_path:    str
    file_type:    str    # pdf | dwg | dxf | png | svg | tiff
    description:  Optional[str] = None
    uploaded_at:  datetime = Field(default_factory=datetime.utcnow)

    rfp: Optional["RFP"] = Relationship(back_populates="drawings")   # type: ignore[name-defined]
