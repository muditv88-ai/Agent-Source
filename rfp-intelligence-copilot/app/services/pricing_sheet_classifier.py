"""
pricing_sheet_classifier.py  v1.0

Sheet-level and workbook-level classifier for pricing intelligence.

Given an openpyxl worksheet (or raw row data), determines:
  - Whether it is a PRICING sheet vs technical / supplier-info / cover
  - The header row index
  - Column role mapping → canonical fields
  - Row roles (header / instruction / data / subtotal / total / blank)
  - Confidence score

Used by pricing_parser.py and pricing_agent.py before extraction begins.
"""
import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Sheet-type keyword signals ────────────────────────────────────────────────
_PRICING_SHEET_SIGNALS = [
    "price", "pricing", "commercial", "cost", "quote", "quotation",
    "rate card", "rate sheet", "bill of materials", "bom", "costing",
    "unit cost", "total cost", "annual value", "contract value",
]

_NON_PRICING_SIGNALS = [
    "technical", "quality", "regulatory", "compliance", "supplier info",
    "general info", "certifications", "instructions", "cover page",
    "questionnaire", "evaluation", "legal",
]

# ── Canonical column → synonym map ───────────────────────────────────────────
CANONICAL_COLUMNS: Dict[str, List[str]] = {
    # Identifiers
    "sku":                 ["sku", "sku#", "sku #", "item code", "part number", "part#", "item#", "product code", "ref", "reference"],
    "description":         ["description", "drug name", "product name", "item name", "item description", "article", "service", "material"],
    "strength":            ["strength", "dosage", "concentration", "grade"],
    "dosage_form":         ["dosage form", "form", "formulation", "type"],
    "pack_size":           ["pack size", "pack", "quantity per pack", "qty/pack"],
    "pack_type":           ["pack type", "container", "packaging type", "primary pack"],
    "route":               ["route", "route of admin", "administration"],
    "site":                ["site", "mfg site", "manufacturing site", "plant"],
    "market":              ["market", "regulatory market", "target market", "region"],

    # Volume / quantity
    "annual_volume":       ["annual volume", "annual vol", "volume", "annual qty", "yearly volume", "units/year", "units per year"],
    "quantity":            ["quantity", "qty", "units", "order qty", "order quantity"],
    "moq":                 ["moq", "minimum order", "min order qty", "minimum quantity"],

    # Cost components
    "api_cost":            ["api cost", "api", "active ingredient cost", "active pharma ingredient"],
    "rm_cost":             ["rm cost", "raw material", "raw mat cost", "material cost", "excipient cost"],
    "pkg_cost":            ["pkg cost", "packaging cost", "pack cost", "container cost", "label cost"],
    "mfg_cost":            ["mfg cost", "manufacturing cost", "conversion cost", "production cost", "process cost", "labor cost"],
    "overhead":            ["overhead", "overhead cost", "indirect cost", "burden", "opex allocation"],
    "margin":              ["margin", "profit", "profit margin", "markup", "mark-up", "gp", "gross profit"],

    # Totals
    "unit_price":          ["unit price", "unit cost", "price per unit", "cost per unit", "net price", "quoted price", "transfer price", "all-in price"],
    "total_unit_cost":     ["total unit cost", "total cost/unit", "total unit price", "all-in unit cost", "landed unit cost"],
    "annual_contract_value": ["annual contract value", "acv", "annual value", "total annual cost", "yearly contract value", "extended value"],
    "total_price":         ["total price", "total cost", "extended price", "line total", "amount"],

    # Commercial terms
    "currency":            ["currency", "ccy", "curr"],
    "lead_time":           ["lead time", "lead time (weeks)", "delivery lead time", "weeks"],
    "shelf_life":          ["shelf life", "shelf life (months)", "expiry period"],
    "storage_condition":   ["storage", "storage condition", "storage requirements", "temp"],
    "payment_terms":       ["payment terms", "payment", "terms", "credit terms"],
    "incoterms":           ["incoterms", "incoterm", "delivery terms"],
    "validity":            ["validity", "quote validity", "valid until", "price validity", "valid through"],
    "comments":            ["comments", "notes", "remarks", "supplier notes", "supplier comments"],
    "score":               ["score", "internal score", "rating", "internal rating"],
}

# ── Row type detection patterns ───────────────────────────────────────────────
_TOTAL_ROW_SIGNALS   = re.compile(r"\b(grand\s*total|total\s*cost|sub[\s-]?total|total\s*value|sum|aggregate)\b", re.I)
_INSTRUCTION_SIGNALS = re.compile(r"\b(please|complete|fill\s*in|required|mandatory|shaded|highlighted|instruction|note:)\b", re.I)
_SECTION_HEADER_RE   = re.compile(r"^(section\s+[a-z0-9]|part\s+[a-z0-9]|appendix|exhibit|attachment|schedule)\b", re.I)


# ── Public API ────────────────────────────────────────────────────────────────

def classify_sheet(sheet_name: str, rows: List[List[Any]]) -> Dict[str, Any]:
    """
    Given a sheet name and its rows (list of lists), return:
    {
      "is_pricing_sheet": bool,
      "confidence": float (0–1),
      "header_row_idx": int | None,
      "column_map": {col_idx: canonical_field_name},
      "row_roles": ["header"|"data"|"instruction"|"subtotal"|"total"|"blank", ...],
      "signals_found": [str, ...],
      "warnings": [str, ...]
    }
    """
    signals_found = []
    warnings      = []

    # 1. Sheet name check
    name_lower = sheet_name.lower()
    pricing_name_score = sum(1 for s in _PRICING_SHEET_SIGNALS if s in name_lower)
    non_pricing_name   = sum(1 for s in _NON_PRICING_SIGNALS   if s in name_lower)

    if pricing_name_score > 0:
        signals_found.append(f"sheet_name_match:{sheet_name}")
    if non_pricing_name > 0:
        signals_found.append(f"non_pricing_name:{sheet_name}")

    # 2. Find header row (first row with 3+ non-empty cells containing column keywords)
    header_row_idx, column_map = _detect_header_and_columns(rows, signals_found)

    # 3. Content scan — look for numeric density and pricing keywords in top 30 rows
    numeric_cells  = 0
    keyword_hits   = 0
    total_cells    = 0
    for row in rows[:30]:
        for cell in row:
            if cell is None or str(cell).strip() == "":
                continue
            total_cells += 1
            try:
                float(str(cell).replace(",", "").replace("$", "").replace("%", ""))
                numeric_cells += 1
            except ValueError:
                pass
            cell_str = str(cell).lower()
            if any(s in cell_str for s in _PRICING_SHEET_SIGNALS):
                keyword_hits += 1

    numeric_ratio = numeric_cells / total_cells if total_cells > 0 else 0
    if numeric_ratio > 0.2:
        signals_found.append(f"numeric_density:{numeric_ratio:.2f}")
    if keyword_hits > 0:
        signals_found.append(f"keyword_hits:{keyword_hits}")

    # 4. Column map quality
    mapped_canonical = set(column_map.values())
    has_price_col    = bool({"unit_price", "total_unit_cost", "annual_contract_value", "total_price"} & mapped_canonical)
    has_id_col       = bool({"sku", "description"} & mapped_canonical)

    # 5. Confidence scoring
    score = 0.0
    if pricing_name_score > 0:   score += 0.35
    if non_pricing_name > 0:     score -= 0.30
    if has_price_col:            score += 0.30
    if has_id_col:               score += 0.10
    if numeric_ratio > 0.15:     score += 0.15
    if keyword_hits > 2:         score += 0.10
    if len(column_map) >= 4:     score += 0.10
    confidence = max(0.0, min(1.0, score))

    is_pricing = confidence >= 0.40 or (has_price_col and has_id_col)

    # 6. Row roles
    row_roles = _classify_rows(rows, header_row_idx)

    if header_row_idx is None and is_pricing:
        warnings.append("Pricing sheet detected but no clear header row found — LLM fallback recommended")

    return {
        "is_pricing_sheet": is_pricing,
        "confidence":       round(confidence, 3),
        "header_row_idx":   header_row_idx,
        "column_map":       column_map,
        "row_roles":        row_roles,
        "signals_found":    signals_found,
        "warnings":         warnings,
        "mapped_fields":    list(mapped_canonical),
    }


def map_header_row(header_cells: List[Any]) -> Dict[int, str]:
    """
    Given a list of header cell values, return {col_index: canonical_field_name}.
    Unrecognised columns are omitted.
    """
    col_map = {}
    for idx, cell in enumerate(header_cells):
        if cell is None:
            continue
        cell_str = str(cell).strip().lower()
        matched  = _match_canonical(cell_str)
        if matched:
            col_map[idx] = matched
    return col_map


def classify_workbook_sheets(sheet_data: Dict[str, List[List[Any]]]) -> Dict[str, Dict]:
    """
    Given {sheet_name: rows}, classify every sheet.
    Returns {sheet_name: classification_result}.
    """
    results = {}
    for name, rows in sheet_data.items():
        results[name] = classify_sheet(name, rows)
    return results


def get_best_pricing_sheet(sheet_classifications: Dict[str, Dict]) -> Optional[str]:
    """
    From classify_workbook_sheets() output, return the sheet name most likely
    to be the primary pricing sheet, or None if none found.
    """
    candidates = [
        (name, info) for name, info in sheet_classifications.items()
        if info["is_pricing_sheet"]
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[1]["confidence"])[0]


# ── Private helpers ───────────────────────────────────────────────────────────

def _match_canonical(cell_str: str) -> Optional[str]:
    """Return canonical field name if cell_str matches any synonym, else None."""
    for canonical, synonyms in CANONICAL_COLUMNS.items():
        for syn in synonyms:
            if syn in cell_str or cell_str in syn:
                return canonical
    return None


def _detect_header_and_columns(
    rows: List[List[Any]], signals: List[str]
) -> Tuple[Optional[int], Dict[int, str]]:
    """
    Scan first 15 rows for a header. A header row is one where ≥3 cells
    match canonical column synonyms.
    Returns (header_row_idx, column_map).
    """
    for row_idx, row in enumerate(rows[:15]):
        col_map = {}
        for col_idx, cell in enumerate(row):
            if cell is None:
                continue
            cell_str = str(cell).strip().lower()
            matched  = _match_canonical(cell_str)
            if matched:
                col_map[col_idx] = matched
        if len(col_map) >= 3:
            signals.append(f"header_row:{row_idx}_cols:{len(col_map)}")
            return row_idx, col_map
    return None, {}


def _classify_rows(rows: List[List[Any]], header_row_idx: Optional[int]) -> List[str]:
    """Classify each row as header/data/instruction/subtotal/total/blank."""
    roles = []
    for idx, row in enumerate(rows):
        if idx == header_row_idx:
            roles.append("header")
            continue
        non_empty = [c for c in row if c is not None and str(c).strip() != ""]
        if len(non_empty) == 0:
            roles.append("blank")
            continue
        row_text = " ".join(str(c) for c in non_empty)
        if _TOTAL_ROW_SIGNALS.search(row_text):
            roles.append("total")
        elif _INSTRUCTION_SIGNALS.search(row_text) or _SECTION_HEADER_RE.match(row_text):
            roles.append("instruction")
        elif len(non_empty) <= 2 and not any(
            c for c in non_empty
            if re.match(r"^[\d.,\-$€£]+$", str(c).strip())
        ):
            roles.append("instruction")
        else:
            roles.append("data")
    return roles
