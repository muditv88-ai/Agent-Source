"""
communication_engine.py

Backward-compatibility shim.
All logic has moved to smtp_dispatcher.py (renamed for clarity).
This module re-exports everything so existing imports continue to work
without any changes to callers.

Do NOT add new logic here — use smtp_dispatcher.py directly.
"""
from app.services.smtp_dispatcher import (  # noqa: F401
    send_email,
    build_fallback,
    draft_clarification_email,
    FALLBACK_TEMPLATES,
)
