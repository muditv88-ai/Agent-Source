"""
agent_logs.py

In-memory ring buffer for agent activity events.
The frontend polls GET /agent-logs every 3 s to display
the live Agent Analytics strip.

Usage (from any route / service):
    from app.api.routes.agent_logs import push_log
    push_log(agent_id="technical", status="running", message="Scoring supplier responses...")

Auth behaviour:
  - Authenticated users  → receive up to `limit` real log entries.
  - Unauthenticated      → receive [] (empty list, no 401).
    This prevents a 401 flood in browser devtools while the frontend
    is initialising its auth session on first load.
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Deque, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.services.auth_service import get_current_user_optional

# ── Types ────────────────────────────────────────────────────────────
AgentStatus = Literal["running", "complete", "queued", "idle", "error"]


class AgentLogEntry(BaseModel):
    id: str
    agent_id: str
    status: AgentStatus
    message: Optional[str] = None
    confidence: Optional[int] = None
    duration_ms: Optional[int] = None
    timestamp: int  # unix ms


# ── Ring buffer (last 100 events, shared across all requests) ────────
_LOG_BUFFER: Deque[AgentLogEntry] = deque(maxlen=100)


def push_log(
    agent_id: str,
    status: AgentStatus,
    message: Optional[str] = None,
    confidence: Optional[int] = None,
    duration_ms: Optional[int] = None,
) -> AgentLogEntry:
    """Call this from any route or service to record an agent event."""
    entry = AgentLogEntry(
        id=str(uuid.uuid4()),
        agent_id=agent_id,
        status=status,
        message=message,
        confidence=confidence,
        duration_ms=duration_ms,
        timestamp=int(time.time() * 1000),
    )
    _LOG_BUFFER.appendleft(entry)
    return entry


# ── Router ───────────────────────────────────────────────────────────
router = APIRouter(tags=["Agent Logs"])


@router.get("", response_model=list[AgentLogEntry])
async def list_agent_logs(
    limit: int = 50,
    current_user=Depends(get_current_user_optional),
):
    """
    Return the most recent `limit` agent log entries (newest first).
    Returns [] for unauthenticated requests instead of 401 —
    prevents console noise while the frontend session is initialising.
    """
    if not current_user:
        return []
    return list(_LOG_BUFFER)[:limit]

