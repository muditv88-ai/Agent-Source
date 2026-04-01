"""
schemas/drawings.py

Pydantic models for /drawings endpoints.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class DrawingAttach(BaseModel):
    drawing_id: str
    rfp_id: str
    line_item_id: Optional[str] = None
    description: Optional[str] = None


class DrawingResponse(BaseModel):
    drawing_id: str
    filename: str
    file_type: str
    storage_path: str          # GCS URI or local path
    public_url: Optional[str] = None
    rfp_id: Optional[str] = None
    line_item_id: Optional[str] = None
    description: Optional[str] = None
    uploaded_at: str
