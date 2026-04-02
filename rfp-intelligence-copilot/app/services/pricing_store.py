"""
pricing_store.py
Thin persistence layer for pricing cost-model data.

load_cost_model(project_id)  -> dict | None
save_cost_model(project_id, cost_model: dict) -> None

The cost model is stored as "pricing_result.json" inside the project
metadata directory (managed by project_store.load_metadata /
save_metadata).
"""
from __future__ import annotations

from typing import Optional

from app.services.project_store import load_metadata, save_metadata

_PRICING_FILE = "pricing_result.json"


def load_cost_model(project_id: str) -> Optional[dict]:
    """Return the saved cost model for *project_id*, or None if not found."""
    data = load_metadata(project_id, _PRICING_FILE)
    if not data:
        return None
    # The /pricing/analyze endpoint wraps everything in a top-level dict;
    # the cost_model itself may be nested under the "cost_model" key.
    return data.get("cost_model") or data or None


def save_cost_model(project_id: str, result: dict) -> None:
    """Persist the full pricing result (including cost_model) for *project_id*."""
    save_metadata(project_id, _PRICING_FILE, result)
