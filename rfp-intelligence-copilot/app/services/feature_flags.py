"""
feature_flags.py

Thin service layer for reading and toggling per-project feature flags.
All reads return safe defaults if flags have not been initialised,
so calling this on any existing project is always safe.

v3.1: Flags are now actively checked by agents.
  - CopilotAgent checks: 'function_calling', 'chatbot_actions'
  - CommsAgent checks:   'email_dry_run'
  - ScoringEngine checks: 'new_analysis_engine'

Usage:
    from app.services.feature_flags import flag_enabled, get_flags, set_flag

    if flag_enabled(project_id, "pricing_scenarios"):
        ...
"""
from app.services.project_store import get_feature_flags, set_feature_flags

# ---------------------------------------------------------------------------
# Default flag values — single source of truth
# ---------------------------------------------------------------------------
DEFAULTS = {
    # Chat
    "chatbot_actions":        True,   # Allow CopilotAgent to trigger agent tool calls
    "function_calling":       True,   # Use OpenAI function-calling in CopilotAgent (False = keyword fallback)

    # Analysis
    "new_analysis_engine":    False,  # Use experimental AIScorer v2 (set True to test)
    "pricing_scenarios":      True,   # Enable what-if pricing scenarios
    "structured_rfp_view":    False,  # Show structured RFP section editor in UI

    # Communications
    "email_dry_run":          False,  # Draft emails but never actually send via SMTP

    # Audit
    "audit_logging":          True,   # Write all actions to audit_logger
}

# Human-readable descriptions for the /admin/flags endpoint
FLAG_DESCRIPTIONS = {
    "chatbot_actions":     "Allow the copilot to trigger agent actions (generate RFP, send email, etc.)",
    "function_calling":    "Use OpenAI function-calling in the Copilot (False = keyword routing fallback)",
    "new_analysis_engine": "Use experimental LLM-based technical scoring engine",
    "pricing_scenarios":   "Enable what-if scenario modelling in the pricing module",
    "structured_rfp_view": "Show the structured RFP section editor in the front-end",
    "email_dry_run":       "Draft emails without sending — safe for test/staging environments",
    "audit_logging":       "Write all user and agent actions to the audit log",
}


def get_flags(project_id: str) -> dict:
    """Return all feature flags for a project. Missing flags are filled with defaults."""
    stored = get_feature_flags(project_id)
    # Merge defaults so any new flags added to DEFAULTS are immediately available
    return {**DEFAULTS, **stored}


def flag_enabled(project_id: str, key: str) -> bool:
    """
    Return True if the named flag is enabled for the project.
    Returns the default value if the flag has never been set.
    """
    flags = get_flags(project_id)
    return bool(flags.get(key, DEFAULTS.get(key, False)))


def set_flag(project_id: str, key: str, value: bool) -> dict:
    """
    Set a single flag and return the full updated flags dict.
    Silently ignores unknown flag keys to prevent typo-driven flag creation.
    """
    if key not in DEFAULTS:
        return get_flags(project_id)  # Unknown key — return current state unchanged
    return set_feature_flags(project_id, {key: value})


def describe_flags() -> dict:
    """Return all flag keys with their default values and descriptions."""
    return {
        k: {"default": v, "description": FLAG_DESCRIPTIONS.get(k, "")}
        for k, v in DEFAULTS.items()
    }
