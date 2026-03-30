"""
pricing_parser.py

Extracts pricing tables from supplier documents.
Supports: Excel (.xlsx/.xls), CSV, PDF with tables, plain text.
Auto-detects structure type: line_item, flat_rate, category_based, rate_card, mixed.
Falls back to LLM extraction when structural parsing yields zero prices.
"""
import os
import re
import json
import time
from typing import Any
from openai import OpenAI

# ── LLM client (same as ai_scorer.py) ────────────────────────────────────────────────
_client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)
MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

# ── Structure type labels ─────────────────────────────────────────────────────────────
STRUCTURE_LINE_ITEM = "line_item"
STRUCTURE_FLAT_RATE = "flat_rate"
STRUCTURE_CATEGORY  = "category_based"
STRUCTURE_RATE_CARD = "rate_card"
STRUCTURE_MIXED     = "mixed"


# ── LLM extraction fallback ─────────────────────────────────────────────────────────
_LLM_PRICING_PROMPT = """
You are a procurement data extraction assistant.
Extract ALL pricing information from the supplier document text below.

Return ONLY a JSON array of line items. Each item must have:
- "description": string (item/service name)
- "quantity": number (default 1 if not specified)
- "unit_price": number (price per unit, 0 if not found)
- "total": number (quantity * unit_price, or the total as stated)
- "category": string (e.g. "Software", "Services", "Hardware", "Support" — infer from context)
- "notes": string (any conditions, discounts, or caveats)

Rules:
- Extract EVERY item that has a price or cost associated with it
- Convert all currencies to numbers (strip symbols, commas)
- If only a total is given with no unit price, set unit_price = total and quantity = 1
- Do NOT include items with zero price unless explicitly stated as 0
- If the document has no pricing at all, return an empty array []

Document text (truncated to 6000 chars):
"""


def _llm_extract_pricing(full_text: str, supplier_name: str, max_retries: int = 3) -> list[dict]:
    """Use LLM to extract pricing line items from unstructured text."""
    truncated = full_text[:6000]
    prompt    = _LLM_PRICING_PROMPT + truncated

    delay = 15.0
    for attempt in range(max_retries + 1):
        try:
            response = _client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": "detailed thinking off"},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2048,
            )
            raw = response.choices[0].message.content or ""
            raw = raw.strip()
            # Strip markdown fences
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$",          "", raw)
            raw = raw.strip()

            items = json.loads(raw)
            if not isinstance(items, list):
                items = items.get("line_items") or items.get("items") or []

            # Sanitise and derive totals
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
                    if total == 0:
                        continue  # skip truly empty items
                    clean.append({
                        "description": str(item.get("description", "")).strip(),
                        "quantity":    qty,
                        "unit_price":  unit_price,
                        "total":       total,
                        "category":    str(item.get("category", "General")).strip(),
                        "notes":       str(item.get("notes", "")).strip(),
                    })
                except (TypeError, ValueError):
                    continue
            return clean

        except Exception as e:
            msg = str(e).lower()
            if ("429" in msg or "rate limit" in msg or "too many" in msg) and attempt < max_retries:
                time.sleep(delay)
                delay *= 2
                continue
            # On non-rate-limit errors or exhausted retries, return empty
            return []

    return []


# ── Helpers ────────────────────────────────────────────────────────────────────────────
def _clean_number(val: Any) -> float | None:
    if val is None:
        return None
    s = str(val).strip().replace(",", "").replace("$", "").replace("£", "").replace("€", "").replace("₹", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def _detect_structure(headers: list[str], rows: list[dict]) -> str:
    h = " ".join(headers).lower()
    has_sku        = any(k in h for k in ["sku", "item", "product", "part", "line", "description"])
    has_qty        = any(k in h for k in ["qty", "quantity", "units", "volume"])
    has_unit_price = any(k in h for k in ["unit price", "unit cost", "rate", "price per", "cost per"])
    has_category   = any(k in h for k in ["category", "section", "group", "type"])
    has_role       = any(k in h for k in ["role", "resource", "level", "grade", "tier", "day rate", "hourly"])
    has_flat       = any(k in h for k in ["one-time", "setup", "implementation", "annual", "monthly", "recurring", "subscription", "licence", "license"])
    if has_role and has_unit_price:    return STRUCTURE_RATE_CARD
    if has_flat and not has_sku:       return STRUCTURE_FLAT_RATE
    if has_category and has_unit_price and not has_sku: return STRUCTURE_CATEGORY
    if has_sku and has_unit_price:     return STRUCTURE_LINE_ITEM
    if sum([has_sku, has_qty, has_unit_price, has_category, has_role, has_flat]) >= 3:
        return STRUCTURE_MIXED
    return STRUCTURE_LINE_ITEM


def _parse_excel_bytes(content: bytes, filename: str) -> dict:
    import openpyxl, io
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    all_sheets = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows_raw = list(ws.iter_rows(values_only=True))
        if not rows_raw:
            continue
        header_row_idx = 0
        for i, row in enumerate(rows_raw):
            if len([c for c in row if c is not None and str(c).strip()]) >= 2:
                header_row_idx = i
                break
        headers   = [str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(rows_raw[header_row_idx])]
        data_rows = [dict(zip(headers, row)) for row in rows_raw[header_row_idx + 1:] if not all(c is None for c in row)]
        if data_rows:
            all_sheets.append({"sheet_name": sheet_name, "headers": headers, "rows": data_rows})
    return {"source": "excel", "sheets": all_sheets}


def _parse_csv_text(text: str) -> dict:
    import csv, io
    reader  = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    return {"source": "csv", "sheets": [{"sheet_name": "Sheet1", "headers": list(headers), "rows": list(reader)}]}


def _extract_tables_from_text(text: str) -> dict:
    lines  = text.split("\n")
    table_blocks, current_block = [], []
    for line in lines:
        s = line.strip()
        if "|" in s or re.search(r'\d+[\.,]\d+.*\d+[\.,]\d+', s):
            current_block.append(s)
        else:
            if len(current_block) >= 3:
                table_blocks.append(current_block)
            current_block = []
    if len(current_block) >= 3:
        table_blocks.append(current_block)

    sheets = []
    for idx, block in enumerate(table_blocks):
        if any("|" in l for l in block):
            parsed_rows = [[c.strip() for c in l.split("|") if c.strip()] for l in block]
            parsed_rows = [r for r in parsed_rows if r]
            if len(parsed_rows) >= 2:
                hdrs = parsed_rows[0]
                rows = [dict(zip(hdrs, r)) for r in parsed_rows[1:]]
                sheets.append({"sheet_name": f"Table_{idx+1}", "headers": hdrs, "rows": rows})
        else:
            parsed_rows = [re.split(r'\s{2,}', l.strip()) for l in block]
            parsed_rows = [r for r in parsed_rows if len(r) >= 2]
            if len(parsed_rows) >= 2:
                hdrs = parsed_rows[0]
                rows = [dict(zip(hdrs, r)) for r in parsed_rows[1:]]
                sheets.append({"sheet_name": f"Table_{idx+1}", "headers": hdrs, "rows": rows})
    return {"source": "text", "sheets": sheets}


def _normalise_sheet(sheet: dict, supplier_name: str) -> dict:
    headers  = sheet.get("headers", [])
    rows     = sheet.get("rows", [])
    structure = _detect_structure(headers, rows)
    h_lower  = [h.lower() for h in headers]

    def find_col(*candidates):
        for c in candidates:
            for i, h in enumerate(h_lower):
                if c in h:
                    return headers[i]
        return None

    desc_col   = find_col("description", "item", "product", "service", "sku", "name", "role", "resource", "category", "section")
    qty_col    = find_col("qty", "quantity", "units", "volume", "hours", "days")
    price_col  = find_col("unit price", "unit cost", "rate", "price", "cost", "fee", "amount")
    total_col  = find_col("total", "extended", "subtotal", "line total")
    cat_col    = find_col("category", "group", "section", "type")
    notes_col  = find_col("notes", "comments", "remarks", "conditions")

    line_items = []
    for row in rows:
        desc       = str(row.get(desc_col, "")).strip()  if desc_col  else ""
        qty        = _clean_number(row.get(qty_col))     if qty_col   else 1.0
        unit_price = _clean_number(row.get(price_col))  if price_col else None
        total      = _clean_number(row.get(total_col))  if total_col else None
        category   = str(row.get(cat_col, "")).strip()  if cat_col   else sheet.get("sheet_name", "")
        notes      = str(row.get(notes_col, "")).strip() if notes_col else ""

        if not desc and unit_price is None and total is None:
            continue
        if total is None and unit_price is not None and qty is not None:
            total = round(unit_price * qty, 2)
        if unit_price is None and total is not None and qty:
            unit_price = round(total / qty, 2)

        line_items.append({
            "description": desc,
            "quantity":    qty or 1.0,
            "unit_price":  unit_price or 0.0,
            "total":       total or 0.0,
            "category":    category,
            "notes":       notes,
        })
    return {
        "sheet_name":     sheet.get("sheet_name", "Pricing"),
        "structure_type": structure,
        "supplier_name":  supplier_name,
        "headers":        headers,
        "line_items":     line_items,
    }


# ── Main entry point ─────────────────────────────────────────────────────────────────
def extract_pricing_from_document(file_path: str, supplier_name: str, full_text: str = "") -> dict:
    import os
    ext = os.path.splitext(file_path)[1].lower()
    raw = {"source": "unknown", "sheets": []}

    try:
        if ext in (".xlsx", ".xls"):
            with open(file_path, "rb") as f:
                raw = _parse_excel_bytes(f.read(), file_path)
        elif ext == ".csv":
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                raw = _parse_csv_text(f.read())
        else:
            if full_text:
                raw = _extract_tables_from_text(full_text)
    except Exception as e:
        raw = {"source": "error", "sheets": [], "error": str(e)}

    normalised_sheets = [
        _normalise_sheet(sheet, supplier_name)
        for sheet in raw.get("sheets", [])
        if sheet.get("rows")
    ]

    if not normalised_sheets and full_text:
        text_raw = _extract_tables_from_text(full_text)
        normalised_sheets = [
            _normalise_sheet(sheet, supplier_name)
            for sheet in text_raw.get("sheets", [])
            if sheet.get("rows")
        ]

    all_items     = [item for sheet in normalised_sheets for item in sheet["line_items"]]
    total_priced  = sum(i["total"] for i in all_items)
    used_llm      = False

    # ── LLM fallback: fire when structural parsing yields no priced items ──
    if total_priced == 0 and full_text:
        llm_items = _llm_extract_pricing(full_text, supplier_name)
        if llm_items:
            all_items    = llm_items
            total_priced = sum(i["total"] for i in llm_items)
            used_llm     = True
            normalised_sheets = [{
                "sheet_name":     "LLM Extracted",
                "structure_type": "mixed",
                "supplier_name":  supplier_name,
                "headers":        ["description", "quantity", "unit_price", "total", "category", "notes"],
                "line_items":     llm_items,
            }]

    structure_types    = list({s["structure_type"] for s in normalised_sheets})
    overall_structure  = structure_types[0] if len(structure_types) == 1 else "mixed"
    warnings           = []
    if total_priced == 0:
        warnings.append("No pricing tables detected — use the Correct button to enter prices manually")
    if used_llm:
        warnings.append("Prices extracted by AI — please verify values in the Price Matrix tab")

    return {
        "supplier_name":   supplier_name,
        "structure_type":  overall_structure,
        "sheets":          normalised_sheets,
        "all_line_items":  all_items,
        "total_cost":      round(total_priced, 2),
        "source_format":   raw.get("source", "unknown"),
        "parse_warnings":  warnings,
    }
