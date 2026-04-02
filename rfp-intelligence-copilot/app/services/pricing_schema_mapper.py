"""
pricing_schema_mapper.py  v1.0

Converts raw extracted rows (after sheet classification + column mapping)
into a canonical PricingSheet JSON object.

Canonical schema:
{
  "workbook_type": "rfp_template" | "supplier_response" | "unknown",
  "source_sheet":  str,
  "currency":      str,
  "pricing_sheets": [...],    # one per detected pricing sheet
  "line_items": [
    {
      "item_id":       str,   # SKU or row index
      "description":   str,
      "strength":      str,
      "dosage_form":   str,
      "pack_size":     str,
      "pack_type":     str,
      "site":          str,
      "market":        str,
      "annual_volume": float,
      "moq":           float,
      "cost_components": {
        "api_cost":  float|None,
        "rm_cost":   float|None,
        "pkg_cost":  float|None,
        "mfg_cost":  float|None,
        "overhead":  float|None,
        "margin":    float|None,
      },
      "unit_price":              float|None,
      "total_unit_cost":         float|None,
      "annual_contract_value":   float|None,
      "lead_time_weeks":         float|None,
      "shelf_life_months":       float|None,
      "storage_condition":       str,
      "payment_terms":           str,
      "validity":                str,
      "supplier_comments":       str,
      "is_buyer_prefilled":      bool,
      "is_supplier_filled":      bool,
      "missing_fields":          [str],
      "extra_fields":            {str: Any},
      "confidence":              float,
    }
  ],
  "summary": {
    "total_line_items":     int,
    "filled_by_supplier":   int,
    "missing_totals":       int,
    "has_cost_breakdown":   bool,
    "detected_currency":    str,
    "grand_total":          float|None,
  },
  "validation_flags": [...],  # from pricing_validation.py
  "raw_column_map":   {col_idx: canonical_field},
}
"""
import re
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Fields that MUST be supplier-filled (not buyer-prefilled)
_SUPPLIER_FILL_FIELDS = {
    "api_cost", "rm_cost", "pkg_cost", "mfg_cost", "overhead",
    "margin", "unit_price", "total_unit_cost", "annual_contract_value",
    "lead_time", "shelf_life", "storage_condition", "payment_terms",
    "validity", "comments",
}

# Cost component fields for breakdown detection
_COST_COMPONENT_FIELDS = {"api_cost", "rm_cost", "pkg_cost", "mfg_cost", "overhead", "margin"}

# Fields that qualify as "total" cost
_TOTAL_COST_FIELDS = {"unit_price", "total_unit_cost", "annual_contract_value", "total_price"}


def _clean_number(val: Any) -> Optional[float]:
    if val is None:
        return None
    s = re.sub(r"[$£€₹%,\s]", "", str(val).strip())
    s = s.replace("(", "-").replace(")", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def map_rows_to_schema(
    rows: List[List[Any]],
    column_map: Dict[int, str],
    row_roles: List[str],
    sheet_name: str = "",
    source_type: str = "unknown",
) -> Dict[str, Any]:
    """
    Convert classified rows + column_map into canonical line items.

    Args:
        rows:        All rows from the sheet (list of lists)
        column_map:  {col_idx: canonical_field_name} from classifier
        row_roles:   ["header","data","instruction","total","blank", ...] per row
        sheet_name:  Name of the source sheet
        source_type: "rfp_template" | "supplier_response" | "unknown"

    Returns:  canonical pricing schema dict
    """
    line_items    = []
    grand_total   = None
    detected_curr = ""

    for row_idx, (row, role) in enumerate(zip(rows, row_roles)):
        if role not in ("data",):
            # Check total rows for grand total
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
            # Detect currency from any currency column
            if not detected_curr:
                curr_col = next(
                    (ci for ci, f in column_map.items() if f == "currency"), None
                )
                if curr_col is not None and curr_col < len(row):
                    detected_curr = _clean_str(row[curr_col])

    # Post-process: derive annual_contract_value if missing
    for item in line_items:
        _derive_missing_totals(item)

    # Summary
    filled_count  = sum(1 for i in line_items if i["is_supplier_filled"])
    missing_total = sum(1 for i in line_items if i["total_unit_cost"] is None and
                        item["annual_contract_value"] is None)
    has_breakdown = any(
        any(v is not None for v in i["cost_components"].values())
        for i in line_items
    )

    return {
        "workbook_type":  source_type,
        "source_sheet":   sheet_name,
        "currency":       detected_curr or "USD",
        "line_items":     line_items,
        "summary": {
            "total_line_items":   len(line_items),
            "filled_by_supplier": filled_count,
            "missing_totals":     missing_total,
            "has_cost_breakdown": has_breakdown,
            "detected_currency":  detected_curr or "USD",
            "grand_total":        grand_total,
        },
        "raw_column_map":  {str(k): v for k, v in column_map.items()},
    }


def _extract_line_item(
    row: List[Any],
    column_map: Dict[int, str],
    row_idx: int,
) -> Optional[Dict[str, Any]]:
    """Extract one canonical line item from a data row."""

    def get(field: str) -> Any:
        col = next((ci for ci, f in column_map.items() if f == field), None)
        if col is not None and col < len(row):
            return row[col]
        return None

    # Must have at least a description or SKU
    sku  = _clean_str(get("sku"))
    desc = _clean_str(get("description"))
    if not sku and not desc:
        return None

    # Cost components
    components = {
        "api_cost": _clean_number(get("api_cost")),
        "rm_cost":  _clean_number(get("rm_cost")),
        "pkg_cost": _clean_number(get("pkg_cost")),
        "mfg_cost": _clean_number(get("mfg_cost")),
        "overhead": _clean_number(get("overhead")),
        "margin":   _clean_number(get("margin")),
    }

    unit_price   = _clean_number(get("unit_price"))
    total_unit   = _clean_number(get("total_unit_cost"))
    acv          = _clean_number(get("annual_contract_value"))
    annual_vol   = _clean_number(get("annual_volume")) or _clean_number(get("quantity")) or 1.0
    lead_time    = _clean_number(get("lead_time"))
    shelf_life   = _clean_number(get("shelf_life"))

    # Supplier-filled detection: has any cost or total value
    supplier_vals = [unit_price, total_unit, acv] + list(components.values())
    is_supplier   = any(v is not None and v > 0 for v in supplier_vals)

    # Missing required fields
    missing = []
    if unit_price is None and total_unit is None:
        missing.append("unit_price / total_unit_cost")
    if not desc:
        missing.append("description")

    # Extra (unmapped) columns
    mapped_cols = set(column_map.keys())
    extra = {}
    for col_idx, val in enumerate(row):
        if col_idx not in mapped_cols and val is not None and str(val).strip():
            extra[f"col_{col_idx}"] = val

    item_id = sku or f"ROW_{row_idx}"

    return {
        "item_id":             item_id,
        "description":         desc or sku,
        "strength":            _clean_str(get("strength")),
        "dosage_form":         _clean_str(get("dosage_form")),
        "pack_size":           _clean_str(get("pack_size")),
        "pack_type":           _clean_str(get("pack_type")),
        "site":                _clean_str(get("site")),
        "market":              _clean_str(get("market")),
        "annual_volume":       annual_vol,
        "moq":                 _clean_number(get("moq")),
        "cost_components":     components,
        "unit_price":          unit_price,
        "total_unit_cost":     total_unit,
        "annual_contract_value": acv,
        "lead_time_weeks":     lead_time,
        "shelf_life_months":   shelf_life,
        "storage_condition":   _clean_str(get("storage_condition")),
        "payment_terms":       _clean_str(get("payment_terms")),
        "validity":            _clean_str(get("validity")),
        "supplier_comments":   _clean_str(get("comments")),
        "is_buyer_prefilled":  not is_supplier,
        "is_supplier_filled":  is_supplier,
        "missing_fields":      missing,
        "extra_fields":        extra,
        "confidence":          0.9 if not missing else 0.6,
    }


def _derive_missing_totals(item: Dict[str, Any]) -> None:
    """Fill in derivable totals using cost components or volume × unit price."""
    comp = item["cost_components"]
    comp_vals = [v for v in comp.values() if v is not None]

    # If we have cost components but no total_unit_cost, sum them
    if item["total_unit_cost"] is None and len(comp_vals) >= 2:
        item["total_unit_cost"] = round(sum(comp_vals), 4)

    # Use unit_price as total_unit_cost fallback
    if item["total_unit_cost"] is None and item["unit_price"] is not None:
        item["total_unit_cost"] = item["unit_price"]

    # Derive ACV = total_unit_cost × annual_volume
    if item["annual_contract_value"] is None and item["total_unit_cost"] is not None:
        vol = item["annual_volume"] or 1
        item["annual_contract_value"] = round(item["total_unit_cost"] * vol, 2)
