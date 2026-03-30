"""Chat agent route - context-aware conversation with analysis results."""
import json
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from app.services.chat_agent import chat_with_agent

router = APIRouter()

META_DIR = Path("metadata")


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    rfp_id: Optional[str] = None
    analysis_context: Optional[Dict[str, Any]] = None  # frontend passes current results


class ChatResponse(BaseModel):
    message: str
    action: Optional[Dict[str, Any]] = None


@router.post("/message", response_model=ChatResponse)
async def chat_message(req: ChatRequest):
    """Send a message to the procurement AI agent."""

    # Build context: combine rfp questions (if available) + any analysis passed by frontend
    context: Dict[str, Any] = {}

    if req.rfp_id:
        questions_path = META_DIR / f"{req.rfp_id}_questions.json"
        if questions_path.exists():
            context["rfp_questions"] = json.loads(questions_path.read_text())

    if req.analysis_context:
        context["analysis_results"] = req.analysis_context

    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    result = chat_with_agent(messages, context if context else None)

    return ChatResponse(
        message=result.get("message", "I couldn't process that. Please try again."),
        action=result.get("action"),
    )
