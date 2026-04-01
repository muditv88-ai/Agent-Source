"""
schemas/chat.py

Pydantic models for /chat endpoints.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str    # "user" | "assistant" | "system"
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    project_id: Optional[str] = None
    context: Dict[str, Any] = {}
    use_copilot_agent: bool = True


class ChatResponse(BaseModel):
    message: str
    action: Optional[Dict[str, Any]] = None
    project_id: Optional[str] = None
