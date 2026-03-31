"""
audit_logger.py

Immutable audit trail for all chatbot actions and scenario executions.
Entries are append-only and stored per-project in audit_log.json.

All mutations triggered via the chatbot (rescore, weight adjust,
pricing scenario, exclude supplier) are logged here.

Usage:
    from app.services.audit_logger import log_action, get_log

    log_action(
        project_id = "abc-123",
        actor      = "user@email.com",
        action     = "pricing_scenario",
        module     = "pricing",
        payload    = {"scenario_type": "custom", "total_cost": 45000},
        reversible = False,
    )
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.services.project_store import save_audit_log, load_audit_log
from app.services.feature_flags import flag_enabled


def log_action(
    project_id: str,
    action:     str,
    module:     str          = "",
    actor:      str          = "system",
    payload:    Dict[str, Any] = None,
    reversible: bool         = False,
) -> dict:
    """
    Write one audit entry.
    Silently skips if audit_logging feature flag is disabled for the project.
    Returns the entry dict (useful for tests / debugging).
    """
    if not flag_enabled(project_id, "audit_logging"):
        return {}

    entry = {
        "entry_id":   str(uuid.uuid4())[:8],
        "project_id": project_id,
        "actor":      actor,
        "action":     action,
        "module":     module,
        "payload":    payload or {},
        "reversible": reversible,
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }
    save_audit_log(project_id, entry)
    return entry


def get_log(project_id: str, limit: int = 50) -> list:
    """
    Return the most recent audit entries for a project.
    Returns [] if logging was never enabled or no entries exist.
    """
    return load_audit_log(project_id, limit=limit)
