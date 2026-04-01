"""
ProjectFile — persists metadata about every file stored in GCS,
linked to a project_id (and optionally an rfp_id or supplier_id).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return str(uuid.uuid4())


class ProjectFile(SQLModel, table=True):
    __tablename__ = "project_file"

    id:           str      = Field(default_factory=_uuid, primary_key=True)

    # Ownership
    project_id:   str      = Field(index=True)
    user_id:      str      = Field(index=True)       # owner account

    # Optional FK links (not enforced as FK — keeps schema flexible)
    rfp_id:       Optional[str] = Field(default=None, index=True)
    supplier_id:  Optional[str] = Field(default=None, index=True)

    # File identity
    filename:     str            # original filename
    display_name: Optional[str] = None   # user-editable label

    # Category determines GCS path segment
    # values: rfp_templates | supplier_responses | drawings | misc
    category:     str

    content_type: str      = Field(default="application/octet-stream")
    size_bytes:   int      = Field(default=0)

    # GCS blob name (full path inside the bucket)
    gcs_path:     str

    # Analysis state
    # none | pending | complete | error
    analysis_status: str   = Field(default="none")
    analysis_result: Optional[str] = None   # JSON-encoded result

    created_at:   datetime = Field(default_factory=datetime.utcnow)
    updated_at:   datetime = Field(default_factory=datetime.utcnow)
