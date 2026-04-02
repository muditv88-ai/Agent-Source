"""
pricing_sheet_classifier.py  v2.0

Handles every layout an RFP workbook can produce:

  CASE 1 — Dedicated pricing sheet
    Pure pricing tab. Handled exactly as v1.0.

  CASE 2 — Single combined workbook (most common real-world scenario)
    One workbook file contains MULTIPLE tabs:
    Tab 1: Supplier Info / Cover Page
    Tab 2: Technical Questions
    Tab 3: Commercial Price Sheet       ← we want this one
    Tab 4: Terms & Conditions
    Strategy: classify_workbook_sheets() scores every tab, pick highest.

  CASE 3 — Mixed/combined sheet (hardest case)
    A SINGLE tab contains BOTH technical questions AND pricing rows,
    often separated by:
      - A section header row (e.g. "SECTION C — COMMERCIAL PRICING")
      - A visual divider (blank row, merged cell, bold text)
      - A new header row mid-sheet
    Strategy: zone_split_sheet() scans for mid-sheet header transitions
    and splits the single tab into named zones. Each zone is classified
    independently. Pricing zones are extracted; technical zones are
    handed to the technical analysis pipeline.

  CASE 4 — Horizontal mixing (rare)
    Pricing columns appear to the RIGHT of technical answer columns
    in the same row grid (e.g. col A-F = tech questions, col G-K = pricing).
    Strategy: column_domain_split() uses column-level keyword scoring to
    split column ranges into "pricing domain" vs "technical domain".
    Only pricing-domain columns are passed to the schema mapper.

Returns for each sheet:
  {
    "is_pricing_sheet": bool,
    "confidence": float,
    "sheet_type": "pricing" | "technical" | "supplier_info" | "mixed" | "cover" | "other",
    "zones": [ZoneResult],       # always populated; 1 zone for pure sheets
    "header_row_idx": int | None,
    "column_map": {col_idx: canonical_field},
    "row_roles": [str],
    "signals_found": [str],
    "warnings": [str],
    "mapped_fields": [str],
  }
"""

import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Keyword signals ───────────────────────────────────────────────────────────
_PRICING_SHEET_SIGNALS = [
    "price", "pricing", "commercial", "cost", "quote", "quotation",
    "rate card", "rate sheet", "bill of materials", "bom", "costing",
    "unit cost", "total cost", "annual value", "contract value",
]
_TECHNICAL_SIGNALS = [
    "technical", "quality", "regulatory", "compliance", "certifications",
    "questionnaire", "evaluation", "specification", "qualification",
    "manufacturing process", "gmp", "pharmacopeia", "stability",
    "analytical", "coa", "dossier", "dmf", "batch", "impurity",
]
_SUPPLIER_INFO_SIGNALS = [
    "supplier info", "general info", "company info", "vendor info",
    "contact", "address", "registration", "bank details", "about us",
]
_COVER_SIGNALS = [
    "cover page", "introduction", "instructions", "how to", "read me",
    "index", "table of contents", "contents",
]

# Zone-boundary section headers (mid-sheet transitions)
_ZONE_BOUNDARY_RE = re.compile(
    r"^(section\s+[a-z0-9ivx]+|part\s+[a-z0-9ivx]+|appendix\s+[a-z0-9]"
    r"|commercial|pricing|technical|quality|regulatory"
    r"|schedule\s+[a-z0-9]|exhibit\s+[a-z0-9]|attachment\s+[a-z0-9]"
    r"|(?:section|tab|sheet)\s*[:\-–]\s*\w+)",
    re.I,
)

# ── Canonical column map ──────────────────────────────────────────────────────
CANONICAL_COLUMNS: Dict[str, List[str]] = {
    "sku":                   ["sku", "sku#", "sku #", "item code", "part number", "part#", "item#", "product code", "ref", "reference"],
    "description":           ["description", "drug name", "product name", "item name", "item description", "article", "service", "material"],
    "strength":              ["strength", "dosage", "concentration", "grade"],
    "dosage_form":           ["dosage form", "form", "formulation", "type"],
    "pack_size":             ["pack size", "pack", "quantity per pack", "qty/pack"],
    "pack_type":             ["pack type", "container", "packaging type", "primary pack"],
    "route":                 ["route", "route of admin", "administration"],
    "site":                  ["site", "mfg site", "manufacturing site", "plant"],
    "market":                ["market", "regulatory market", "target market", "region"],
    "annual_volume":         ["annual volume", "annual vol", "volume", "annual qty", "yearly volume", "units/year", "units per year"],
    "quantity":              ["quantity", "qty", "units", "order qty", "order quantity"],
    "moq":                   ["moq", "minimum order", "min order qty", "minimum quantity"],
    "api_cost":              ["api cost", "api", "active ingredient cost", "active pharma ingredient"],
    "rm_cost":               ["rm cost", "raw material", "raw mat cost", "material cost", "excipient cost"],
    "pkg_cost":              ["pkg cost", "packaging cost", "pack cost", "container cost", "label cost"],
    "mfg_cost":              ["mfg cost", "manufacturing cost", "conversion cost", "production cost", "process cost", "labor cost"],
    "overhead":              ["overhead", "overhead cost", "indirect cost", "burden", "opex allocation"],
    "margin":                ["margin", "profit", "profit margin", "markup", "mark-up", "gp", "gross profit"],
    "unit_price":            ["unit price", "unit cost", "price per unit", "cost per unit", "net price", "quoted price", "transfer price", "all-in price"],
    "total_unit_cost":       ["total unit cost", "total cost/unit", "total unit price", "all-in unit cost", "landed unit cost"],
    "annual_contract_value": ["annual contract value", "acv", "annual value", "total annual cost", "yearly contract value", "extended value"],
    "total_price":           ["total price", "total cost", "extended price", "line total", "amount"],
    "currency":              ["currency", "ccy", "curr"],
    "lead_time":             ["lead time", "lead time (weeks)", "delivery lead time", "weeks"],
    "shelf_life":            ["shelf life", "shelf life (months)", "expiry period"],
    "storage_condition":     ["storage", "storage condition", "storage requirements", "temp"],
    "payment_terms":         ["payment terms", "payment", "terms", "credit terms"],
    "incoterms":             ["incoterms", "incoterm", "delivery terms"],
    "validity":              ["validity", "quote validity", "valid until", "price validity", "valid through"],
    "comments":              ["comments", "notes", "remarks", "supplier notes", "supplier comments"],
}

_PRICE_FIELDS    = {"unit_price", "total_unit_cost", "annual_contract_value", "total_price"}
_ID_FIELDS       = {"sku", "description"}
_TECH_COL_HINTS  = {"score", "response", "answer", "requirement", "meets", "yes/no", "rating", "comment"}

_TOTAL_ROW_RE    = re.compile(r"\b(grand\s*total|total\s*cost|sub[\s-]?total|total\s*value|sum|aggregate)\b", re.I)
_INSTRUCTION_RE  = re.compile(r"\b(please|complete|fill\s*in|required|mandatory|shaded|highlighted|instruction|note:)\b", re.I)


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def classify_sheet(sheet_name: str, rows: List[List[Any]]) -> Dict[str, Any]:
    """
    Full classification for a single sheet.
    Automatically detects:
      - pure pricing sheet
      - pure technical sheet
      - mixed sheet (has pricing zones AND technical zones)
      - horizontal mixed (pricing columns live beside technical columns)
    """
    signals_found: List[str] = []
    warnings:      List[str] = []

    # Step 1: Sheet name scoring
    name_lower = sheet_name.lower()
    pricing_name  = sum(1 for s in _PRICING_SHEET_SIGNALS  if s in name_lower)
    tech_name     = sum(1 for s in _TECHNICAL_SIGNALS      if s in name_lower)
    supinfo_name  = sum(1 for s in _SUPPLIER_INFO_SIGNALS  if s in name_lower)
    cover_name    = sum(1 for s in _COVER_SIGNALS          if s in name_lower)

    if pricing_name:  signals_found.append(f"sheet_name_pricing:{sheet_name}")
    if tech_name:     signals_found.append(f"sheet_name_technical:{sheet_name}")
    if supinfo_name:  signals_found.append(f"sheet_name_supplier_info:{sheet_name}")

    # Step 2: Detect zone boundaries mid-sheet (vertical mixed layout)
    zones         = zone_split_sheet(rows)
    pricing_zones = [z for z in zones if z["zone_type"] == "pricing"]
    tech_zones    = [z for z in zones if z["zone_type"] == "technical"]
    is_mixed      = len(pricing_zones) > 0 and len(tech_zones) > 0

    if is_mixed:
        signals_found.append(f"mixed_sheet:pricing_zones={len(pricing_zones)},tech_zones={len(tech_zones)}")
        warnings.append(
            f"Combined sheet detected: {len(pricing_zones)} pricing zone(s) and {len(tech_zones)} "
            f"technical zone(s). Pricing extraction will target rows {pricing_zones[0]['row_start']}–"
            f"{pricing_zones[0]['row_end']} only."
        )

    # Step 3: For pricing zones, run header + column detection on just those rows
    primary_zone_rows = pricing_zones[0]["rows"] if pricing_zones else rows
    header_row_idx, column_map = _detect_header_and_columns(primary_zone_rows, signals_found)

    # Adjust header_row_idx back to absolute sheet row if a zone was sliced
    if pricing_zones:
        abs_offset = pricing_zones[0]["row_start"]
        header_row_idx = (header_row_idx + abs_offset) if header_row_idx is not None else None

    # Step 4: Horizontal column domain split
    h_pricing_cols, h_tech_cols = column_domain_split(column_map, primary_zone_rows)
    if h_tech_cols and h_pricing_cols:
        signals_found.append(f"horizontal_split:pricing_cols={sorted(h_pricing_cols)},tech_cols={sorted(h_tech_cols)}")
        # Keep only pricing-domain columns in column_map
        column_map = {k: v for k, v in column_map.items() if k in h_pricing_cols}
        warnings.append(
            f"Horizontal mixed columns: pricing columns {sorted(h_pricing_cols)}, "
            f"technical columns {sorted(h_tech_cols)} excluded from pricing extraction."
        )

    # Step 5: Content statistics
    scan_rows    = primary_zone_rows[:40]
    numeric_cells = keyword_hits = total_cells = 0
    for row in scan_rows:
        for cell in row:
            if cell is None or str(cell).strip() == "":
                continue
            total_cells += 1
            try:
                float(str(cell).replace(",", "").replace("$", "").replace("%", ""))
                numeric_cells += 1
            except ValueError:
                pass
            if any(s in str(cell).lower() for s in _PRICING_SHEET_SIGNALS):
                keyword_hits += 1

    numeric_ratio = numeric_cells / total_cells if total_cells else 0
    if numeric_ratio > 0.15: signals_found.append(f"numeric_density:{numeric_ratio:.2f}")
    if keyword_hits > 0:     signals_found.append(f"keyword_hits:{keyword_hits}")

    # Step 6: Column quality
    mapped_canonical  = set(column_map.values())
    has_price_col     = bool(_PRICE_FIELDS & mapped_canonical)
    has_id_col        = bool(_ID_FIELDS    & mapped_canonical)

    # Step 7: Confidence scoring
    score = 0.0
    if pricing_name > 0:          score += 0.35
    if tech_name > 0:             score -= 0.25
    if supinfo_name > 0:          score -= 0.20
    if cover_name > 0:            score -= 0.30
    if pricing_zones:             score += 0.35    # found a pricing zone even in mixed sheet
    if has_price_col:             score += 0.30
    if has_id_col:                score += 0.10
    if numeric_ratio > 0.15:      score += 0.10
    if keyword_hits > 2:          score += 0.10
    if len(column_map) >= 4:      score += 0.05
    confidence = max(0.0, min(1.0, score))

    is_pricing = confidence >= 0.40 or (has_price_col and has_id_col) or bool(pricing_zones)

    # Step 8: Sheet type label
    if is_mixed:
        sheet_type = "mixed"
    elif is_pricing:
        sheet_type = "pricing"
    elif tech_name or tech_zones:
        sheet_type = "technical"
    elif supinfo_name:
        sheet_type = "supplier_info"
    elif cover_name:
        sheet_type = "cover"
    else:
        sheet_type = "other"

    # Step 9: Row roles (full sheet)
    row_roles = _classify_rows(rows, header_row_idx)

    if header_row_idx is None and is_pricing:
        warnings.append("Pricing sheet/zone detected but no clear header row found — LLM fallback recommended")

    return {
        "is_pricing_sheet": is_pricing,
        "confidence":       round(confidence, 3),
        "sheet_type":       sheet_type,
        "zones":            zones,
        "pricing_zones":    pricing_zones,
        "tech_zones":       tech_zones,
        "header_row_idx":   header_row_idx,
        "column_map":       column_map,
        "row_roles":        row_roles,
        "signals_found":    signals_found,
        "warnings":         warnings,
        "mapped_fields":    list(mapped_canonical),
    }


def zone_split_sheet(rows: List[List[Any]]) -> List[Dict[str, Any]]:
    """
    Detect vertical zone boundaries within a single sheet.

    A new zone starts when:
      (a) A section-header cell is found (matches _ZONE_BOUNDARY_RE), OR
      (b) A new header row appears mid-sheet (3+ column keyword matches after
          a non-header section), OR
      (c) A dense blank-row gap (2+ consecutive blanks) separates sections.

    Returns a list of zone dicts:
    {
      "zone_name":  str,
      "zone_type":  "pricing" | "technical" | "supplier_info" | "cover" | "unknown",
      "row_start":  int,     ← absolute row index in the sheet
      "row_end":    int,
      "rows":       [[...]], ← the actual row data for this zone
      "confidence": float,
    }
    """
    if not rows:
        return []

    boundary_indices: List[Tuple[int, str]] = [(0, "START")]
    consecutive_blanks = 0

    for i, row in enumerate(rows):
        non_empty = [c for c in row if c is not None and str(c).strip()]
        if not non_empty:
            consecutive_blanks += 1
            if consecutive_blanks >= 2 and i > 5:
                boundary_indices.append((i, "BLANK_GAP"))
            continue
        consecutive_blanks = 0

        # Section header detection: first non-empty cell matches boundary pattern
        first_cell = str(non_empty[0]).strip()
        if _ZONE_BOUNDARY_RE.match(first_cell) and len(non_empty) <= 3:
            boundary_indices.append((i, first_cell))
            continue

        # Mid-sheet header row: row looks like column headers
        if i > 5:
            mapped = sum(1 for c in row if c and _match_canonical(str(c).strip().lower()))
            if mapped >= 3:
                prev_boundaries = [b[0] for b in boundary_indices]
                if i - max(prev_boundaries) > 3:
                    boundary_indices.append((i, f"HEADER_ROW:{first_cell}"))

    # Build zones from boundary pairs
    zones = []
    for idx, (start, label) in enumerate(boundary_indices):
        end = boundary_indices[idx + 1][0] if idx + 1 < len(boundary_indices) else len(rows)
        zone_rows = rows[start:end]
        zone_type = _classify_zone_type(label, zone_rows)
        zone_conf = _zone_confidence(zone_rows)
        zones.append({
            "zone_name":  label,
            "zone_type":  zone_type,
            "row_start":  start,
            "row_end":    end,
            "rows":       zone_rows,
            "confidence": zone_conf,
        })

    return zones


def column_domain_split(
    column_map: Dict[int, str],
    rows: List[List[Any]],
) -> Tuple[set, set]:
    """
    When a single header row maps BOTH pricing and technical columns,
    split column indices into (pricing_cols, technical_cols).

    Returns (pricing_col_set, technical_col_set).
    If no technical columns detected, returns (all_mapped_cols, empty_set).
    """
    pricing_cols  = set()
    technical_cols= set()

    for col_idx, canonical in column_map.items():
        if canonical in _TECH_COL_HINTS or _is_technical_column(col_idx, rows):
            technical_cols.add(col_idx)
        else:
            pricing_cols.add(col_idx)

    # Only apply split if we actually found both domains
    if not technical_cols:
        return set(column_map.keys()), set()

    return pricing_cols, technical_cols


def classify_workbook_sheets(sheet_data: Dict[str, List[List[Any]]]) -> Dict[str, Dict]:
    """Classify every sheet in a workbook. Returns {sheet_name: classification}."""
    return {name: classify_sheet(name, rows) for name, rows in sheet_data.items()}


def get_best_pricing_sheet(sheet_classifications: Dict[str, Dict]) -> Optional[str]:
    """Return the sheet name with highest pricing confidence, or None."""
    candidates = [
        (name, info) for name, info in sheet_classifications.items()
        if info["is_pricing_sheet"]
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[1]["confidence"])[0]


def get_all_pricing_zones(
    sheet_name: str,
    classification: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    For a classified sheet, return ALL pricing zones (including from mixed sheets).
    Use this when you need to extract pricing from a combined tab.
    """
    return classification.get("pricing_zones") or (
        [{"zone_name": sheet_name, "zone_type": "pricing",
          "row_start": 0, "row_end": None, "rows": None,
          "confidence": classification["confidence"]}]
        if classification["is_pricing_sheet"] else []
    )


def map_header_row(header_cells: List[Any]) -> Dict[int, str]:
    """Map a header row list to {col_index: canonical_field_name}."""
    return {
        idx: matched
        for idx, cell in enumerate(header_cells)
        if cell and (matched := _match_canonical(str(cell).strip().lower()))
    }


# ═════════════════════════════════════════════════════════════════════════════
# PRIVATE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _match_canonical(cell_str: str) -> Optional[str]:
    for canonical, synonyms in CANONICAL_COLUMNS.items():
        for syn in synonyms:
            if syn in cell_str or cell_str in syn:
                return canonical
    return None


def _detect_header_and_columns(
    rows: List[List[Any]],
    signals_found: List[str],
) -> Tuple[Optional[int], Dict[int, str]]:
    """Find the header row and build column_map from it."""
    best_idx     = None
    best_map: Dict[int, str] = {}
    best_score   = 0

    for i, row in enumerate(rows[:20]):
        mapped = {}
        for col_idx, cell in enumerate(row):
            if cell is None:
                continue
            cell_str = str(cell).strip().lower()
            canonical = _match_canonical(cell_str)
            if canonical:
                mapped[col_idx] = canonical

        if len(mapped) > best_score:
            best_score = len(mapped)
            best_idx   = i
            best_map   = mapped

    if best_idx is not None and best_score >= 2:
        signals_found.append(f"header_row:{best_idx} cols_mapped:{best_score}")
        return best_idx, best_map

    return None, {}


def _classify_rows(rows: List[List[Any]], header_row_idx: Optional[int]) -> List[str]:
    roles = []
    for i, row in enumerate(rows):
        non_empty = [c for c in row if c is not None and str(c).strip()]
        if not non_empty:
            roles.append("blank")
            continue
        if i == header_row_idx:
            roles.append("header")
            continue
        first = str(non_empty[0]).strip()
        if _TOTAL_ROW_RE.search(first):
            roles.append("total")
            continue
        if _INSTRUCTION_RE.search(first) and len(non_empty) <= 4:
            roles.append("instruction")
            continue
        if _ZONE_BOUNDARY_RE.match(first) and len(non_empty) <= 3:
            roles.append("section_header")
            continue
        # Data row: has at least one numeric cell (could be a price or volume)
        has_numeric = any(
            _try_float(str(c).replace(",", "").replace("$", "").replace("%", ""))
            for c in non_empty
        )
        roles.append("data" if has_numeric else "text")
    return roles


def _classify_zone_type(label: str, zone_rows: List[List[Any]]) -> str:
    label_lower = label.lower()
    if any(s in label_lower for s in _PRICING_SHEET_SIGNALS):
        return "pricing"
    if any(s in label_lower for s in _TECHNICAL_SIGNALS):
        return "technical"
    if any(s in label_lower for s in _SUPPLIER_INFO_SIGNALS):
        return "supplier_info"
    if any(s in label_lower for s in _COVER_SIGNALS):
        return "cover"

    # Fall back to content scan of zone rows
    price_hits = tech_hits = 0
    for row in zone_rows[:15]:
        for cell in row:
            if not cell:
                continue
            s = str(cell).lower()
            price_hits += sum(1 for sig in _PRICING_SHEET_SIGNALS if sig in s)
            tech_hits  += sum(1 for sig in _TECHNICAL_SIGNALS      if sig in s)

    if price_hits > tech_hits and price_hits > 2:
        return "pricing"
    if tech_hits > price_hits and tech_hits > 2:
        return "technical"
    return "unknown"


def _zone_confidence(zone_rows: List[List[Any]]) -> float:
    """Quick numeric density check for a zone's rows."""
    numeric = total = 0
    for row in zone_rows[:20]:
        for cell in row:
            if cell is None or str(cell).strip() == "":
                continue
            total += 1
            if _try_float(str(cell).replace(",", "").replace("$", "").replace("%", "")):
                numeric += 1
    return round(numeric / total, 2) if total else 0.0


def _is_technical_column(col_idx: int, rows: List[List[Any]]) -> bool:
    """Check if a column's data cells look like qualitative / text answers."""
    text_count = numeric_count = 0
    for row in rows[1:20]:
        if col_idx >= len(row):
            continue
        val = row[col_idx]
        if val is None or str(val).strip() == "":
            continue
        if _try_float(str(val).replace(",", "").replace("$", "").replace("%", "")):
            numeric_count += 1
        else:
            text_count += 1
    # If column is > 70% text and < 30% numeric, it's likely a tech column
    total = text_count + numeric_count
    return text_count / total > 0.7 if total >= 3 else False


def _try_float(s: str) -> bool:
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False
