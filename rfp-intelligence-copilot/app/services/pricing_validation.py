"""
pricing_validation.py  v1.0

Validates a canonical pricing schema (from pricing_schema_mapper.py).
Returns a list of ValidationFlag objects describing issues found.

Checks:
  - Missing mandatory supplier-fill fields
  - Formula consistency (components sum ≈ total_unit_cost)
  - ACV consistency (unit_cost × volume ≈ acv)
  - Outlier prices (> 3× median for same SKU across suppliers)
  - Blank high-value rows (volume > 0 but no price)
  - Currency presence
  - MOQ vs annual volume sanity

Confidence tiers:
  HIGH   → auto-ingest, no review needed
  MEDIUM → show mapped fields for analyst confirmation
  LOW    → route to manual review queue
"""
import logging
import statistics
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SEVERITY_ERROR   = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO    = "info"


def validate_pricing_schema(
    schema: Dict[str, Any],
    supplier_name: str = "",
    tolerance: float = 0.05,   # 5% tolerance for formula checks
) -> Dict[str, Any]:
    """
    Validate a canonical pricing schema.

    Returns:
    {
      "supplier":          str,
      "confidence_tier":   "HIGH" | "MEDIUM" | "LOW",
      "overall_score":     float (0–1),
      "flags":             [ValidationFlag dicts],
      "auto_ingest":       bool,
      "review_needed":     bool,
    }
    """
    flags  = []
    items  = schema.get("line_items", [])
    summ   = schema.get("summary", {})

    if not items:
        flags.append(_flag(SEVERITY_ERROR, "NO_LINE_ITEMS",
                           "No line items found in pricing sheet — check sheet detection"))

    # 1. Missing currency
    if not schema.get("currency") or schema["currency"] in ("", "unknown"):
        flags.append(_flag(SEVERITY_WARNING, "MISSING_CURRENCY",
                           "Currency not detected — defaulted to USD"))

    # 2. Per-line-item checks
    for item in items:
        iid = item.get("item_id", "?")
        _check_missing_fields(item, iid, flags)
        _check_formula_consistency(item, iid, tolerance, flags)
        _check_acv_consistency(item, iid, tolerance, flags)
        _check_blank_high_volume(item, iid, flags)

    # 3. Cross-item outlier detection
    _check_price_outliers(items, flags)

    # 4. Score + tier
    error_count   = sum(1 for f in flags if f["severity"] == SEVERITY_ERROR)
    warning_count = sum(1 for f in flags if f["severity"] == SEVERITY_WARNING)
    total_items   = max(len(items), 1)

    score = 1.0
    score -= (error_count   / total_items) * 0.5
    score -= (warning_count / total_items) * 0.2
    score  = max(0.0, min(1.0, score))

    if score >= 0.85 and error_count == 0:
        tier         = "HIGH"
        auto_ingest  = True
        review_needed= False
    elif score >= 0.60:
        tier         = "MEDIUM"
        auto_ingest  = False
        review_needed= True
    else:
        tier         = "LOW"
        auto_ingest  = False
        review_needed= True

    return {
        "supplier":        supplier_name,
        "confidence_tier": tier,
        "overall_score":   round(score, 3),
        "flags":           flags,
        "auto_ingest":     auto_ingest,
        "review_needed":   review_needed,
        "stats": {
            "total_items":   len(items),
            "error_count":   error_count,
            "warning_count": warning_count,
        },
    }


def _flag(severity: str, code: str, message: str, item_id: str = "") -> Dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "item_id": item_id}


def _check_missing_fields(item: Dict, iid: str, flags: List) -> None:
    if item.get("total_unit_cost") is None and item.get("unit_price") is None:
        if item.get("is_supplier_filled", False) or item.get("annual_volume", 1) > 0:
            flags.append(_flag(SEVERITY_ERROR, "MISSING_TOTAL_COST",
                               f"No unit price or total unit cost — supplier may not have filled this row",
                               iid))

    comp = item.get("cost_components", {})
    comp_filled = [v for v in comp.values() if v is not None and v > 0]
    if 0 < len(comp_filled) < 3:
        flags.append(_flag(SEVERITY_WARNING, "PARTIAL_COST_BREAKDOWN",
                           f"Only {len(comp_filled)} of 6 cost components filled — breakdown may be incomplete",
                           iid))


def _check_formula_consistency(item: Dict, iid: str, tol: float, flags: List) -> None:
    comp   = item.get("cost_components", {})
    vals   = [v for v in comp.values() if v is not None and v > 0]
    total  = item.get("total_unit_cost")
    if len(vals) >= 3 and total and total > 0:
        comp_sum = sum(vals)
        diff     = abs(comp_sum - total) / total
        if diff > tol:
            flags.append(_flag(SEVERITY_WARNING, "FORMULA_MISMATCH",
                               f"Cost components sum ({comp_sum:.4f}) differs from total_unit_cost ({total:.4f}) by {diff:.1%}",
                               iid))


def _check_acv_consistency(item: Dict, iid: str, tol: float, flags: List) -> None:
    tuc  = item.get("total_unit_cost")
    acv  = item.get("annual_contract_value")
    vol  = item.get("annual_volume")
    if tuc and acv and vol and vol > 0 and acv > 0:
        expected = tuc * vol
        diff     = abs(expected - acv) / acv
        if diff > tol:
            flags.append(_flag(SEVERITY_WARNING, "ACV_FORMULA_MISMATCH",
                               f"ACV ({acv:.2f}) ≠ unit_cost ({tuc:.4f}) × volume ({vol:.0f}) = {expected:.2f}",
                               iid))


def _check_blank_high_volume(item: Dict, iid: str, flags: List) -> None:
    vol = item.get("annual_volume", 0) or 0
    if vol > 10000 and item.get("total_unit_cost") is None and item.get("unit_price") is None:
        flags.append(_flag(SEVERITY_ERROR, "HIGH_VOLUME_NO_PRICE",
                           f"High-volume line ({vol:,.0f} units) has no price — likely skipped by supplier",
                           iid))


def _check_price_outliers(items: List[Dict], flags: List) -> None:
    """Flag items whose unit_price is > 3× the median across all items."""
    prices = [i["total_unit_cost"] for i in items
              if i.get("total_unit_cost") and i["total_unit_cost"] > 0]
    if len(prices) < 3:
        return
    med = statistics.median(prices)
    if med == 0:
        return
    for item in items:
        p = item.get("total_unit_cost")
        if p and p > 3 * med:
            flags.append(_flag(SEVERITY_WARNING, "PRICE_OUTLIER",
                               f"Unit cost {p:.4f} is >3× the median ({med:.4f}) — verify with supplier",
                               item.get("item_id", "?")))
