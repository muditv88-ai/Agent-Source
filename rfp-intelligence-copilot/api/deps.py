"""
deps.py
FastAPI dependency providers.

Each request to POST /api/v1/rfp/parse gets a fresh AgentLoop
with a unique session_id so context is fully isolated per call.
"""

import uuid
from agent_loop import AgentLoop


def get_agent_loop() -> AgentLoop:
    """Dependency: returns a new AgentLoop per request."""
    return AgentLoop(session_id=str(uuid.uuid4()))
