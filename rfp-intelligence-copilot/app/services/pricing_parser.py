"""
pricing_parser.py

Extracts pricing tables from supplier documents.
Supports: Excel (.xlsx/.xls), CSV, PDF with tables, plain text.
Auto-detects structure type: line_item, flat_rate, category_based, rate_card, mixed.
"""
import re
import json
from typing import Any


# ── Structure type labels ─────────────────────────────────────────────────────
STRUCTURE_LINE_ITEM     = "line_item"
STRUCTURE_FLAT_RATE     = "flat_rate"
STRUCTURE_CATEGORY      = "category_based"
STRUCTURE_RATE_CARD     = "rate_card"
STRUCTURE_MIXED         = "mixed"


def _clean_number(val: Any) -> float | None:
    """Convert various price formats to float."""
    if val is None:
        return None
    s = str(val).strip().replace(",", "").replace("$", "").replace("£", "").replace("€", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def _detect_structure(headers: list[str], rows: list[dict]) -> str:
    """Heuristically detect the pricing structure from column names and data."""
    h = " ".join(headers).lower()
    
    has_sku        = any(k in h for k in ["sku", "item", "product", "part", "line", "description"])
    has_qty        = any(k in h for k in ["qty", "quantity", "units", "volume"])
    has_unit_price = any(k in h for k in ["unit price", "unit cost", "rate", "price per", "cost per"])
    has_category   = any(k in h for k in ["category", "section", "group", "type"])
    has_role       = any(k in h for k in ["role", "resource", "level", "grade", "tier", "day rate", "hourly"])
    has_flat       = any(k in h for k in ["one-time", "setup", "implementation", "annual", "monthly", "recurring", "subscription", "licence", "license"])

    flags = sum([has_sku, has_qty, has_unit_price, has_category, has_role, has_flat])

    if has_role and has_unit_price:
        return STRUCTURE_RATE_CARD
    if has_flat and not has_sku:
        return STRUCTURE_FLAT_RATE
    if has_category and has_unit_price and not has_sku:
        return STRUCTURE_CATEGORY
    if has_sku and has_unit_price:
        return STRUCTURE_LINE_ITEM
    if flags >= 3:
        return STRUCTURE_MIXED
    return STRUCTURE_LINE_ITEM  # default fallback


def _parse_excel_bytes(content: bytes, filename: str) -> dict:
    """Parse Excel file from bytes."""
    import openpyxl
    import io
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    
    all_sheets = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows_raw = list(ws.iter_rows(values_only=True))
        if not rows_raw:
            continue
        
        # Find header row (first row with >= 2 non-empty cells)
        header_row_idx = 0
        for i, row in enumerate(rows_raw):
            non_empty = [c for c in row if c is not None and str(c).strip()]
            if len(non_empty) >= 2:
                header_row_idx = i
                break
        
        headers = [str(c).strip() if c is not None else f"col_{i}" 
                   for i, c in enumerate(rows_raw[header_row_idx])]
        
        data_rows = []
        for row in rows_raw[header_row_idx + 1:]:
            if all(c is None for c in row):
                continue
            data_rows.append(dict(zip(headers, row)))
        
        if data_rows:
            all_sheets.append({"sheet_name": sheet_name, "headers": headers, "rows": data_rows})
    
    return {"source": "excel", "sheets": all_sheets}


def _parse_csv_text(text: str) -> dict:
    """Parse CSV text content."""
    import csv
    import io
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    rows = list(reader)
    return {"source": "csv", "sheets": [{"sheet_name": "Sheet1", "headers": list(headers), "rows": rows}]}


def _extract_tables_from_text(text: str) -> dict:
    """
    Extract pricing tables from plain text or PDF text.
    Looks for tabular patterns: lines with consistent delimiter spacing or | separators.
    """
    lines = text.split("\n")
    
    # Find blocks that look like tables (pipe-delimited or consistent spacing)
    table_blocks = []
    current_block = []
    
    for line in lines:
        stripped = line.strip()
        # Pipe-delimited or has multiple numbers
        if "|" in stripped or re.search(r'\d+[\.,]\d+.*\d+[\.,]\d+', stripped):
            current_block.append(stripped)
        else:
            if len(current_block) >= 3:
                table_blocks.append(current_block)
            current_block = []
    if len(current_block) >= 3:
        table_blocks.append(current_block)
    
    sheets = []
    for block_idx, block in enumerate(table_blocks):
        # Try to parse as pipe-delimited
        if any("|" in line for line in block):
            parsed_rows = []
            for line in block:
                cells = [c.strip() for c in line.split("|") if c.strip()]
                if cells:
                    parsed_rows.append(cells)
            if len(parsed_rows) >= 2:
                headers = parsed_rows[0]
                rows = [dict(zip(headers, r)) for r in parsed_rows[1:]]
                sheets.append({"sheet_name": f"Table_{block_idx+1}", "headers": headers, "rows": rows})
        else:
            # Space-separated numeric table
            parsed_rows = []
            for line in block:
                cells = re.split(r'\s{2,}', line.strip())
                if len(cells) >= 2:
                    parsed_rows.append(cells)
            if len(parsed_rows) >= 2:
                headers = parsed_rows[0]
                rows = [dict(zip(headers, r)) for r in parsed_rows[1:]]
                sheets.append({"sheet_name": f"Table_{block_idx+1}", "headers": headers, "rows": rows})
    
    return {"source": "text", "sheets": sheets}


def _normalise_sheet(sheet: dict, supplier_name: str) -> dict:
    """
    Normalise a raw sheet into a standard pricing table with:
    - structure_type
    - line_items: [{description, quantity, unit_price, total, category, notes}]
    """
    headers  = sheet.get("headers", [])
    rows     = sheet.get("rows", [])
    structure = _detect_structure(headers, rows)
    
    h_lower = [h.lower() for h in headers]
    
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
        desc      = str(row.get(desc_col, "")).strip()  if desc_col  else ""
        qty       = _clean_number(row.get(qty_col))      if qty_col   else 1.0
        unit_price = _clean_number(row.get(price_col))  if price_col else None
        total     = _clean_number(row.get(total_col))   if total_col else None
        category  = str(row.get(cat_col, "")).strip()   if cat_col   else sheet.get("sheet_name", "")
        notes     = str(row.get(notes_col, "")).strip() if notes_col else ""
        
        if not desc and unit_price is None and total is None:
            continue
        
        # Derive total if missing
        if total is None and unit_price is not None and qty is not None:
            total = round(unit_price * qty, 2)
        # Derive unit_price if missing
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
        "sheet_name":    sheet.get("sheet_name", "Pricing"),
        "structure_type": structure,
        "supplier_name": supplier_name,
        "headers":       headers,
        "line_items":    line_items,
    }


def extract_pricing_from_document(file_path: str, supplier_name: str, full_text: str = "") -> dict:
    """
    Main entry point.
    Reads the file at file_path, extracts pricing tables, normalises them.
    Returns a dict with structure_type, line_items, raw_sheets.
    """
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
            # PDF or DOCX — use full_text extracted by document_parser
            if full_text:
                raw = _extract_tables_from_text(full_text)
    except Exception as e:
        raw = {"source": "error", "sheets": [], "error": str(e)}
    
    # Normalise all sheets
    normalised_sheets = [
        _normalise_sheet(sheet, supplier_name)
        for sheet in raw.get("sheets", [])
        if sheet.get("rows")
    ]
    
    # If no structured tables found, try text fallback
    if not normalised_sheets and full_text:
        text_raw = _extract_tables_from_text(full_text)
        normalised_sheets = [
            _normalise_sheet(sheet, supplier_name)
            for sheet in text_raw.get("sheets", [])
            if sheet.get("rows")
        ]
    
    # Merge all line items, keep sheet breakdown too
    all_items = [item for sheet in normalised_sheets for item in sheet["line_items"]]
    structure_types = list({s["structure_type"] for s in normalised_sheets})
    overall_structure = structure_types[0] if len(structure_types) == 1 else "mixed"
    
    return {
        "supplier_name":   supplier_name,
        "structure_type":  overall_structure,
        "sheets":          normalised_sheets,
        "all_line_items":  all_items,
        "total_cost":      round(sum(i["total"] for i in all_items), 2),
        "source_format":   raw.get("source", "unknown"),
        "parse_warnings":  [] if normalised_sheets else ["No pricing tables detected — manual review recommended"],
    }
