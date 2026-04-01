"""
chat.py  v3.0

Upgrade: chat_with_agent (keyword routing) → CopilotAgent (function-calling loop).
Full backward compatibility preserved: chat_with_agent retained as fallback.

New:
  POST /chat/message   — now powered by CopilotAgent with OpenAI tool-calling
  GET  /chat/audit/{project_id} — chatbot audit trail (unchanged from v2.0)
  GET  /chat/tools     — list tools available to the Copilot agent
"""
import json
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.copilot_agent import CopilotAgent
from app.services.chat_agent import chat_with_agent   # kept as fallback
from app.services.audit_logger import log_action, get_log
from app.services.feature_flags import flag_enabled

router = APIRouter()


# ── Request / response models ─────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str       # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    context:  Optional[Dict[str, Any]] = None
    project_id: Optional[str]          = None
    actor:      Optional[str]          = "user"
    use_copilot_agent: bool            = True   # set False to use legacy chat_with_agent

class ChatResponse(BaseModel):
    message: str
    action:  Optional[Dict[str, Any]] = None


# ════════════════════════════════════════════════════════════════════════════
# PRIMARY ENDPOINT — upgraded to CopilotAgent
# ════════════════════════════════════════════════════════════════════════════

@router.post("/message", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send messages to the SourceIQ Copilot agent.

    v3.0: Powered by CopilotAgent with OpenAI function-calling.
    The agent can invoke tools: generate_rfp, send_communication,
    run_award_scenario, get_analysis_summary.

    Set use_copilot_agent=False to fall back to legacy keyword-routing agent.
    """
    messages = [{"role": m.role, "content": m.content} for m in request.messages]
    context  = request.context or {}
    project_id = request.project_id
    actor = request.actor or "user"

    # Feature flag gate
    block_mutations = (
        project_id is not None
        and not flag_enabled(project_id, "chatbot_actions")
    )

    try:
        if request.use_copilot_agent:
            agent = CopilotAgent()
            result = agent.run({
                "messages": messages,
                "context": context,
                "project_id": project_id,
            })
        else:
            # Legacy fallback — unchanged behaviour
            result = chat_with_agent(messages, context)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    action = result.get("action")

    # Audit log any mutation
    if action and project_id and not block_mutations:
        action_type = action.get("type", "unknown") if isinstance(action, dict) else "unknown"
        module = (
            "pricing"   if action_type in ("price_comparison",) else
            "technical" if action_type in ("run_analysis",) else
            "rfp"       if action_type in ("generate_rfp",) else
            "comms"     if action_type in ("send_communication",) else
            "award"     if action_type in ("award_scenario", "run_award_scenario") else
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

    if block_mutations and action:
        result["message"] += " (Note: chatbot actions are disabled for this project.)"
        result["action"]   = None

    return ChatResponse(
        message = result.get("message", ""),
        action  = result.get("action"),
    )


# ════════════════════════════════════════════════════════════════════════════
# AUDIT ENDPOINT — unchanged from v2.0
# ════════════════════════════════════════════════════════════════════════════

@router.get("/audit/{project_id}")
async def get_chat_audit_log(project_id: str, limit: int = 50):
    """Return chatbot action audit trail for a project."""
    entries = get_log(project_id, limit=min(limit, 200))
    return {
        "project_id": project_id,
        "entries":    entries,
        "count":      len(entries),
    }


# ════════════════════════════════════════════════════════════════════════════
# NEW v3.0 ENDPOINT — introspect available Copilot tools
# ════════════════════════════════════════════════════════════════════════════

@router.get("/tools")
async def list_copilot_tools():
    """Return list of tools the CopilotAgent can invoke."""
    agent = CopilotAgent()
    return {
        "tools": [
            {
                "name": name,
                "description": tool.description,
            }
            for name, tool in agent.tools.items()
        ],
        "count": len(agent.tools),
    }
