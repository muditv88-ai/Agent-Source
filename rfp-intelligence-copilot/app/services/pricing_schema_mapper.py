"""
pricing_schema_mapper.py  v2.0

Extends v1.0 to handle zone-aware extraction:
  - Accepts optional pricing_zones list from the classifier
  - If zones present, extracts line items ONLY from pricing zone rows
  - Merges multi-zone results (e.g. two pricing zones in one combined sheet)
  - Still supports plain row lists for pure pricing sheets (backward compat)
"""
import re
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_COST_COMPONENT_FIELDS = {"api_cost", "rm_cost", "pkg_cost", "mfg_cost", "overhead", "margin"}
_TOTAL_COST_FIELDS     = {"unit_price", "total_unit_cost", "annual_contract_value", "total_price"}


def map_rows_to_schema(
    rows: List[List[Any]],
    column_map: Dict[int, str],
    row_roles: List[str],
    sheet_name: str = "",
    source_type: str = "unknown",
    pricing_zones: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Convert classified rows → canonical PricingSheet JSON.

    If pricing_zones is provided (from classify_sheet on a mixed/combined tab),
    only rows within those zone boundaries are processed for line items.
    This prevents technical-question rows from polluting the pricing schema.
    """

    # Build a set of "allowed" row indices
    if pricing_zones:
        allowed_indices: Optional[set] = set()
        for zone in pricing_zones:
            start = zone["row_start"]
            end   = zone["row_end"] or len(rows)
            allowed_indices.update(range(start, end))
    else:
        allowed_indices = None  # all rows allowed

    line_items   = []
    grand_total  = None
    detected_curr= ""

    for row_idx, (row, role) in enumerate(zip(rows, row_roles)):
        if allowed_indices is not None and row_idx not in allowed_indices:
            continue  # skip rows outside pricing zones

        if role not in ("data",):
            if role == "total":
                for col_idx, field in column_map.items():
                    if field in _TOTAL_COST_FIELDS and col_idx < len(row):
                        val = _clean_number(row[col_idx])
                        if val and val > 0:
                            grand_total = val
            continue

        item = _extract_line_item(row, column_map, row_idx)
        if item:
            line_items.append(item)
            if not detected_curr:
                curr_col = next((ci for ci, f in column_map.items() if f == "currency"), None)
                if curr_col is not None and curr_col < len(row):
                    detected_curr = _clean_str(row[curr_col])

    for item in line_items:
        _derive_missing_totals(item)

    filled_count  = sum(1 for i in line_items if i["is_supplier_filled"])
    missing_total = sum(1 for i in line_items
                        if i["total_unit_cost"] is None and i["annual_contract_value"] is None)
    has_breakdown = any(any(v is not None for v in i["cost_components"].values()) for i in line_items)

    # Zone metadata for UI display
    zone_info = None
    if pricing_zones:
        zone_info = [{"name": z["zone_name"], "rows": f"{z['row_start']}–{z['row_end']}", "confidence": z["confidence"]} for z in pricing_zones]

    return {
        "workbook_type": source_type,
        "source_sheet":  sheet_name,
        "currency":      detected_curr or "USD",
        "line_items":    line_items,
        "extracted_from_zones": zone_info,
        "summary": {
            "total_line_items":   len(line_items),
            "filled_by_supplier": filled_count,
            "missing_totals":     missing_total,
            "has_cost_breakdown": has_breakdown,
            "detected_currency":  detected_curr or "USD",
            "grand_total":        grand_total,
        },
        "raw_column_map": {str(k): v for k, v in column_map.items()},
    }


# ── helpers ───────────────────────────────────────────────────────────────────

def _clean_number(val: Any) -> Optional[float]:
    if val is None:
        return None
    s = re.sub(r"[$£€₹%,\s]", "", str(val).strip()).replace("(", "-").replace(")", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _clean_str(val: Any) -> str:
    return "" if val is None else str(val).strip()


def _extract_line_item(row: List[Any], column_map: Dict[int, str], row_idx: int) -> Optional[Dict[str, Any]]:
    def get(field):
        col = next((ci for ci, f in column_map.items() if f == field), None)
        return row[col] if col is not None and col < len(row) else None

    sku  = _clean_str(get("sku"))
    desc = _clean_str(get("description"))
    if not sku and not desc:
        return None

    components = {k: _clean_number(get(k)) for k in ("api_cost","rm_cost","pkg_cost","mfg_cost","overhead","margin")}
    unit_price  = _clean_number(get("unit_price"))
    total_unit  = _clean_number(get("total_unit_cost"))
    acv         = _clean_number(get("annual_contract_value"))
    annual_vol  = _clean_number(get("annual_volume")) or _clean_number(get("quantity")) or 1.0

    is_supplier = any(v is not None and v > 0 for v in [unit_price, total_unit, acv, *components.values()])
    missing     = []
    if unit_price is None and total_unit is None:
        missing.append("unit_price / total_unit_cost")
    if not desc:
        missing.append("description")

    extra = {f"col_{ci}": val for ci, val in enumerate(row)
             if ci not in column_map and val is not None and str(val).strip()}

    return {
        "item_id": sku or f"ROW_{row_idx}",
        "description": desc or sku,
        "strength": _clean_str(get("strength")),
        "dosage_form": _clean_str(get("dosage_form")),
        "pack_size": _clean_str(get("pack_size")),
        "pack_type": _clean_str(get("pack_type")),
        "site": _clean_str(get("site")),
        "market": _clean_str(get("market")),
        "annual_volume": annual_vol,
        "moq": _clean_number(get("moq")),
        "cost_components": components,
        "unit_price": unit_price,
        "total_unit_cost": total_unit,
        "annual_contract_value": acv,
        "lead_time_weeks": _clean_number(get("lead_time")),
        "shelf_life_months": _clean_number(get("shelf_life")),
        "storage_condition": _clean_str(get("storage_condition")),
        "payment_terms": _clean_str(get("payment_terms")),
        "validity": _clean_str(get("validity")),
        "supplier_comments": _clean_str(get("comments")),
        "is_buyer_prefilled": not is_supplier,
        "is_supplier_filled": is_supplier,
        "missing_fields": missing,
        "extra_fields": extra,
        "confidence": 0.9 if not missing else 0.6,
    }


def _derive_missing_totals(item: Dict[str, Any]) -> None:
    comp_vals = [v for v in item["cost_components"].values() if v is not None]
    if item["total_unit_cost"] is None and len(comp_vals) >= 2:
        item["total_unit_cost"] = round(sum(comp_vals), 4)
    if item["total_unit_cost"] is None and item["unit_price"] is not None:
        item["total_unit_cost"] = item["unit_price"]
    if item["annual_contract_value"] is None and item["total_unit_cost"] is not None:
        vol = item["annual_volume"] or 1
        item["annual_contract_value"] = round(item["total_unit_cost"] * vol, 2)
