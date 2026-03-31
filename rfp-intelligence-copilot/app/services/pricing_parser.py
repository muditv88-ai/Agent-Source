"""
pricing_parser.py v2

Phase 1 (AI): Parse the pricing/commercial sheet, understand its structure,
              identify buyer-defined fields vs supplier-filled fields.
Phase 2 (Python): Return a structured PricingSheet object ready for
                  pure-Python comparison and scenario calculation.

Supports: Excel (.xlsx/.xls), CSV, PDF/text with tables.
Falls back to LLM extraction when structural parsing yields no prices.
"""
import os
import re
import json
import time
from typing import Any
from openai import OpenAI

_client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)
MODEL = "meta/llama-3.3-70b-instruct"   # faster model for parsing

# ── Structure labels ──────────────────────────────────────────────────────────
STRUCTURE_LINE_ITEM = "line_item"
STRUCTURE_FLAT_RATE = "flat_rate"
STRUCTURE_CATEGORY  = "category_based"
STRUCTURE_RATE_CARD = "rate_card"
STRUCTURE_MIXED     = "mixed"

# ── RFP structure understanding prompt ───────────────────────────────────────
_RFP_STRUCTURE_PROMPT = """
You are a procurement data analyst. Analyse the pricing/commercial section below.

Identify:
1. BUYER-DEFINED fields: values pre-filled by the buyer in the RFP template
   (e.g. SKU codes, item descriptions, quantities, specifications, target prices)
2. SUPPLIER-FILLED fields: columns/cells the supplier was asked to complete
   (e.g. unit price, total cost, discount %, delivery lead time, payment terms)
3. The pricing structure type: line_item | flat_rate | category_based | rate_card | mixed
4. The currency (if detectable)
5. Whether there is a Grand Total / Total Cost cell

Return ONLY this JSON:
{
  "structure_type": "line_item",
  "currency": "USD",
  "buyer_fields": ["SKU", "Description", "Quantity"],
  "supplier_fields": ["Unit Price", "Total Price", "Lead Time"],
  "has_grand_total": true,
  "notes": "any important structural observations"
}
"""

# ── LLM line-item extraction prompt ──────────────────────────────────────────
_LLM_PRICING_PROMPT = """
You are a procurement data extraction assistant.
Extract ALL pricing line items from the supplier document text below.

Return ONLY a JSON array. Each item must have:
- "sku": string (SKU/part number if present, else "")
- "description": string (item/service name — required)
- "quantity": number (default 1)
- "unit_price": number (supplier's price per unit, 0 if blank)
- "total": number (quantity * unit_price, or stated total)
- "category": string (infer: Software / Hardware / Services / Support / Logistics)
- "unit": string (each / hour / day / month / year / kg — if stated)
- "is_buyer_defined": true if this row was pre-filled by RFP template, false if supplier-entered
- "notes": string (discounts, conditions, caveats)

Rules:
- Include ALL items with a price. Skip items where both unit_price and total are 0.
- Strip currency symbols and commas from numbers.
- If only total given and qty>1: unit_price = total / qty.
- Return [] if truly no pricing found.

Document text (≤8000 chars):
"""


# ── LLM helpers ───────────────────────────────────────────────────────────────

def _call_llm(prompt: str, max_tokens: int = 1024, max_retries: int = 3) -> str:
    delay = 15.0
    for attempt in range(max_retries + 1):
        try:
            resp = _client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": "detailed thinking off"},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.1,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            msg = str(e).lower()
            if ("429" in msg or "rate limit" in msg) and attempt < max_retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    return ""


def _parse_json(raw: str) -> Any:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw).strip()
    return json.loads(raw)


def _llm_understand_structure(text: str) -> dict:
    """Ask LLM to identify buyer vs supplier fields."""
    prompt = _RFP_STRUCTURE_PROMPT + "\n\nDocument text (≤3000 chars):\n" + text[:3000]
    try:
        raw = _call_llm(prompt, max_tokens=512)
        return _parse_json(raw)
    except Exception:
        return {
            "structure_type": "line_item",
            "currency": "",
            "buyer_fields": [],
            "supplier_fields": [],
            "has_grand_total": False,
            "notes": "Structure auto-detection failed",
        }


def _llm_extract_line_items(text: str, supplier_name: str) -> list:
    """LLM fallback: extract structured line items from unstructured text."""
    prompt = _LLM_PRICING_PROMPT + text[:8000]
    try:
        raw   = _call_llm(prompt, max_tokens=2048)
        items = _parse_json(raw)
        if not isinstance(items, list):
            items = items.get("line_items") or items.get("items") or []
        return _sanitise_items(items)
    except Exception:
        return []


def _sanitise_items(items: list) -> list:
    clean = []
    for item in items:
        try:
            qty        = float(item.get("quantity", 1) or 1)
            unit_price = float(item.get("unit_price", 0) or 0)
            total      = float(item.get("total", 0) or 0)
            if total == 0 and unit_price > 0:
                total = round(qty * unit_price, 2)
            if unit_price == 0 and total > 0:
                unit_price = round(total / qty, 2)
            if total == 0 and unit_price == 0:
                continue
            clean.append({
                "sku":              str(item.get("sku", "")).strip(),
                "description":      str(item.get("description", "")).strip(),
                "quantity":         qty,
                "unit_price":       unit_price,
                "total":            total,
                "category":         str(item.get("category", "General")).strip(),
                "unit":             str(item.get("unit", "each")).strip(),
                "is_buyer_defined": bool(item.get("is_buyer_defined", False)),
                "notes":            str(item.get("notes", "")).strip(),
            })
        except (TypeError, ValueError):
            continue
    return clean


# ── Number cleaner ────────────────────────────────────────────────────────────

def _clean_number(val: Any) -> float | None:
    if val is None:
        return None
    s = re.sub(r"[,$£€₹%]", "", str(val).strip()).replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


# ── Excel / CSV / text parsers ────────────────────────────────────────────────

def _parse_excel_bytes(content: bytes) -> dict:
    import openpyxl, io
    wb   = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    sheets = []
    for sheet_name in wb.sheetnames:
        ws       = wb[sheet_name]
        rows_raw = list(ws.iter_rows(values_only=True))
        if not rows_raw:
            continue
        # Find header row: first row with ≥2 non-empty cells
        hdr_idx = 0
        for i, row in enumerate(rows_raw):
            if len([c for c in row if c is not None and str(c).strip()]) >= 2:
                hdr_idx = i
                break
        headers   = [str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(rows_raw[hdr_idx])]
        data_rows = [
            dict(zip(headers, row))
            for row in rows_raw[hdr_idx + 1:]
            if not all(c is None for c in row)
        ]
        if data_rows:
            sheets.append({"sheet_name": sheet_name, "headers": headers, "rows": data_rows})
    return {"source": "excel", "sheets": sheets}


def _parse_csv_text(text: str) -> dict:
    import csv, io
    reader  = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    return {"source": "csv", "sheets": [{"sheet_name": "Sheet1", "headers": headers, "rows": list(reader)}]}


def _extract_tables_from_text(text: str) -> dict:
    lines = text.split("\n")
    blocks, current = [], []
    for line in lines:
        s = line.strip()
        if "|" in s or re.search(r"\d+[\.,]\d+.*\d+[\.,]\d+", s):
            current.append(s)
        else:
            if len(current) >= 3:
                blocks.append(current)
            current = []
    if len(current) >= 3:
        blocks.append(current)

    sheets = []
    for idx, block in enumerate(blocks):
        if any("|" in l for l in block):
            parsed = [[c.strip() for c in l.split("|") if c.strip()] for l in block]
            parsed = [r for r in parsed if r]
        else:
            parsed = [re.split(r"\s{2,}", l.strip()) for l in block]
            parsed = [r for r in parsed if len(r) >= 2]
        if len(parsed) >= 2:
            hdrs = parsed[0]
            rows = [dict(zip(hdrs, r)) for r in parsed[1:]]
            sheets.append({"sheet_name": f"Table_{idx+1}", "headers": hdrs, "rows": rows})
    return {"source": "text", "sheets": sheets}


# ── Sheet normaliser (structural parsing) ─────────────────────────────────────

def _detect_structure(headers: list) -> str:
    h = " ".join(headers).lower()
    has_sku        = any(k in h for k in ["sku", "item", "product", "part", "line", "description"])
    has_qty        = any(k in h for k in ["qty", "quantity", "units", "volume"])
    has_unit_price = any(k in h for k in ["unit price", "unit cost", "rate", "price per", "cost per"])
    has_category   = any(k in h for k in ["category", "section", "group", "type"])
    has_role       = any(k in h for k in ["role", "resource", "level", "grade", "tier", "day rate", "hourly"])
    has_flat       = any(k in h for k in ["one-time", "setup", "annual", "monthly", "recurring", "subscription", "licence", "license"])
    if has_role and has_unit_price:     return STRUCTURE_RATE_CARD
    if has_flat and not has_sku:        return STRUCTURE_FLAT_RATE
    if has_category and not has_sku:    return STRUCTURE_CATEGORY
    if has_sku and has_unit_price:      return STRUCTURE_LINE_ITEM
    return STRUCTURE_MIXED


def _normalise_sheet(sheet: dict, supplier_name: str, buyer_fields: list, supplier_fields: list) -> dict:
    headers  = sheet.get("headers", [])
    rows     = sheet.get("rows", [])
    h_lower  = [h.lower() for h in headers]

    # Identify buyer_fields vs supplier_fields from LLM analysis
    buyer_cols    = [h for h in headers if any(bf.lower() in h.lower() for bf in buyer_fields)]
    supplier_cols = [h for h in headers if any(sf.lower() in h.lower() for sf in supplier_fields)]

    def _find(*candidates):
        for c in candidates:
            for i, h in enumerate(h_lower):
                if c in h:
                    return headers[i]
        return None

    sku_col    = _find("sku", "part", "code", "item no", "item#")
    desc_col   = _find("description", "item", "product", "service", "name", "role", "resource", "category", "section")
    qty_col    = _find("qty", "quantity", "units", "volume", "hours", "days")
    price_col  = _find("unit price", "unit cost", "rate", "price", "cost", "fee", "amount")
    total_col  = _find("total", "extended", "subtotal", "line total")
    cat_col    = _find("category", "group", "section", "type")
    unit_col   = _find("unit", "uom", "measure")
    notes_col  = _find("notes", "comments", "remarks", "conditions")

    line_items = []
    for row in rows:
        sku        = str(row.get(sku_col, "")).strip()   if sku_col   else ""
        desc       = str(row.get(desc_col, "")).strip()  if desc_col  else ""
        qty        = _clean_number(row.get(qty_col))    if qty_col   else 1.0
        unit_price = _clean_number(row.get(price_col)) if price_col else None
        total      = _clean_number(row.get(total_col)) if total_col else None
        category   = str(row.get(cat_col, "")).strip()  if cat_col   else sheet.get("sheet_name", "")
        unit       = str(row.get(unit_col, "each")).strip() if unit_col else "each"
        notes      = str(row.get(notes_col, "")).strip() if notes_col else ""

        # Determine if this row is buyer-defined or supplier-filled
        is_buyer_defined = bool(desc_col and desc_col in buyer_cols)

        if not desc and unit_price is None and total is None:
            continue
        if total is None and unit_price is not None and qty:
            total = round((unit_price) * (qty or 1), 2)
        if unit_price is None and total is not None and qty:
            unit_price = round(total / (qty or 1), 2)

        line_items.append({
            "sku":            sku,
            "description":    desc,
            "quantity":       qty or 1.0,
            "unit_price":     unit_price or 0.0,
            "total":          total or 0.0,
            "category":       category,
            "unit":           unit,
            "is_buyer_defined": is_buyer_defined,
            "notes":          notes,
        })

    return {
        "sheet_name":     sheet.get("sheet_name", "Pricing"),
        "structure_type": _detect_structure(headers),
        "supplier_name":  supplier_name,
        "headers":        headers,
        "buyer_cols":     buyer_cols,
        "supplier_cols":  supplier_cols,
        "line_items":     line_items,
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def extract_pricing_from_document(
    file_path: str,
    supplier_name: str,
    full_text: str = "",
    rfp_full_text: str = "",
) -> dict:
    """
    Two-phase extraction:
    Phase 1 — LLM analyses structure (buyer vs supplier fields).
    Phase 2 — Python parses the actual data using that structure map.

    Returns:
        supplier_name, structure_type, structure_info (LLM analysis),
        sheets, all_line_items, total_cost, source_format, parse_warnings
    """
    ext = os.path.splitext(file_path)[1].lower()
    raw = {"source": "unknown", "sheets": []}

    # --- Structural parse ---
    try:
        if ext in (".xlsx", ".xls"):
            with open(file_path, "rb") as f:
                raw = _parse_excel_bytes(f.read())
        elif ext == ".csv":
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                raw = _parse_csv_text(f.read())
        else:
            if full_text:
                raw = _extract_tables_from_text(full_text)
    except Exception as e:
        raw = {"source": "error", "sheets": [], "error": str(e)}

    # --- Phase 1: LLM structure understanding ---
    sample_text = full_text[:3000] if full_text else ""
    if not sample_text and raw.get("sheets"):
        # Build text sample from first sheet headers + first 10 rows
        first = raw["sheets"][0]
        sample_text = " | ".join(first.get("headers", [])) + "\n"
        for row in list(first.get("rows", []))[:10]:
            sample_text += " | ".join(str(v) for v in row.values()) + "\n"

    structure_info = _llm_understand_structure(sample_text) if sample_text else {
        "structure_type": "line_item", "currency": "",
        "buyer_fields": [], "supplier_fields": [], "has_grand_total": False, "notes": ""
    }
    buyer_fields    = structure_info.get("buyer_fields", [])
    supplier_fields = structure_info.get("supplier_fields", [])

    # --- Phase 2: Python normalisation ---
    normalised_sheets = [
        _normalise_sheet(sheet, supplier_name, buyer_fields, supplier_fields)
        for sheet in raw.get("sheets", [])
        if sheet.get("rows")
    ]
    if not normalised_sheets and full_text:
        text_raw = _extract_tables_from_text(full_text)
        normalised_sheets = [
            _normalise_sheet(sheet, supplier_name, buyer_fields, supplier_fields)
            for sheet in text_raw.get("sheets", [])
            if sheet.get("rows")
        ]

    all_items    = [item for sheet in normalised_sheets for item in sheet["line_items"]]
    total_priced = sum(i["total"] for i in all_items)
    used_llm     = False

    # --- LLM fallback if structural parse yielded nothing ---
    if total_priced == 0 and full_text:
        llm_items = _llm_extract_line_items(full_text, supplier_name)
        if llm_items:
            all_items    = llm_items
            total_priced = sum(i["total"] for i in llm_items)
            used_llm     = True
            normalised_sheets = [{
                "sheet_name":     "LLM Extracted",
                "structure_type": "mixed",
                "supplier_name":  supplier_name,
                "headers":        ["sku", "description", "quantity", "unit_price", "total", "category", "unit", "notes"],
                "buyer_cols":     buyer_fields,
                "supplier_cols":  supplier_fields,
                "line_items":     llm_items,
            }]

    structure_types = list({s["structure_type"] for s in normalised_sheets})
    warnings = []
    if total_priced == 0:
        warnings.append("No pricing detected — use the Correct button to enter prices manually")
    if used_llm:
        warnings.append("Prices extracted by AI — please verify values in the Price Matrix tab")

    return {
        "supplier_name":   supplier_name,
        "structure_type":  structure_types[0] if len(structure_types) == 1 else "mixed",
        "structure_info":  structure_info,        # buyer vs supplier field map
        "sheets":          normalised_sheets,
        "all_line_items":  all_items,
        "total_cost":      round(total_priced, 2),
        "source_format":   raw.get("source", "unknown"),
        "parse_warnings":  warnings,
        "currency":        structure_info.get("currency", ""),
    }
