"""
feature_flags.py

Thin service layer for reading and toggling per-project feature flags.
All reads return safe defaults if flags have not been initialised,
so calling this on any existing project is always safe.

Usage:
    from app.services.feature_flags import flag_enabled, get_flags, set_flag

    if flag_enabled(project_id, "pricing_scenarios"):
        ...
"""
from app.services.project_store import get_feature_flags, set_feature_flags

# Re-export defaults here so callers have a single source of truth
DEFAULTS = {
    "chatbot_actions":     True,
    "new_analysis_engine": False,
    "pricing_scenarios":   True,
    "structured_rfp_view": False,
    "audit_logging":       True,
}


def get_flags(project_id: str) -> dict:
    """
    Return all feature flags for a project.
    Missing flags are filled with defaults.
    """
    return get_feature_flags(project_id)


def flag_enabled(project_id: str, key: str) -> bool:
    """
    Return True if the named flag is enabled for the project.
    Returns the default value if the flag has never been set.
    """
    flags = get_feature_flags(project_id)
    return bool(flags.get(key, DEFAULTS.get(key, False)))


def set_flag(project_id: str, key: str, value: bool) -> dict:
    """
    Set a single flag and return the full updated flags dict.
    Silently ignores unknown flag keys.
    """
    return set_feature_flags(project_id, {key: value})
