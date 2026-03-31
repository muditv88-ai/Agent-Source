"""
chat.py  v2.0

Existing POST /chat endpoint preserved and backward-compatible.
New endpoint added:
  GET /chat/audit/{project_id}  — return chatbot action audit trail

Context enrichment (v2.0):
  - Accepts typed ChatContext in addition to the existing untyped dict
  - Logs all chatbot mutations to audit_logger
  - Checks feature flags before executing mutations
"""
import json
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.chat_agent import chat_with_agent
from app.services.audit_logger import log_action, get_log
from app.services.feature_flags import flag_enabled

router = APIRouter()


# ── Request / response models ─────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str      # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    context:  Optional[Dict[str, Any]] = None   # existing untyped context — kept for compat
    project_id: Optional[str]          = None   # v2.0: explicit project_id for audit logging
    actor:      Optional[str]          = "user" # v2.0: user identity for audit trail

class ChatResponse(BaseModel):
    message: str
    action:  Optional[Dict[str, Any]] = None


# ════════════════════════════════════════════════════════════════════════════
# EXISTING ENDPOINT — extended, backward-compatible
# ════════════════════════════════════════════════════════════════════════════

@router.post("/message", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
):
    """
    Send messages to the procurement AI agent.

    v2.0 additions (all optional — existing callers unaffected):
      - project_id: enables audit logging of mutations
      - actor:      identity logged in audit trail
      - feature flag check: chatbot_actions flag must be True to execute mutations
    """
    messages = [{"role": m.role, "content": m.content} for m in request.messages]
    context  = request.context or {}

    # Feature flag gate — only blocks mutations, not read-only Q&A
    project_id  = request.project_id
    actor       = request.actor or "user"
    block_mutations = (
        project_id is not None
        and not flag_enabled(project_id, "chatbot_actions")
    )

    result = chat_with_agent(messages, context)

    action = result.get("action")

    # v2.0: audit log any mutation action
    if action and project_id and not block_mutations:
        action_type = action.get("type", "unknown")
        module = (
            "pricing"   if action_type in ("pricing_scenario",) else
            "technical" if action_type in ("rescore", "adjust_weight", "exclude_supplier") else
            "general"
        )
        log_action(
            project_id = project_id,
            action     = action_type,
            module     = module,
            actor      = actor,
            payload    = action,
            reversible = action_type in ("rescore", "adjust_weight"),
        )

    # If mutations are blocked, strip the action but still return the message
    if block_mutations and action:
        result["message"] += " (Note: chatbot actions are disabled for this project.)"
        result["action"]   = None

    return ChatResponse(
        message = result.get("message", ""),
        action  = result.get("action"),
    )


# ════════════════════════════════════════════════════════════════════════════
# NEW v2.0 ENDPOINT
# ════════════════════════════════════════════════════════════════════════════

@router.get("/audit/{project_id}")
async def get_chat_audit_log(project_id: str, limit: int = 50):
    """
    Return the chatbot action audit trail for a project.
    Entries include: actor, action type, module, payload, timestamp.
    """
    entries = get_log(project_id, limit=min(limit, 200))
    return {
        "project_id": project_id,
        "entries":    entries,
        "count":      len(entries),
    }
