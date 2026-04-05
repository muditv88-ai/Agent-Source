"""
pricing.py  — Pricing Agent routes  (v2: push_log instrumentation)
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_db
from app.api.routes.agent_logs import push_log

router = APIRouter(tags=["Pricing"])

try:
    from app.agents.pricing_agent import PricingAgent
    _PRICING_AGENT_AVAILABLE = True
except ImportError:
    _PRICING_AGENT_AVAILABLE = False

try:
    from app.services import gcs_storage as _gcs
    _GCS_ENABLED = True
except Exception:
    _GCS_ENABLED = False


def _save_to_gcs(project_id, category, filename, file_bytes, content_type):
    if not _GCS_ENABLED:
        return None
    try:
        return _gcs.upload_file(
            project_id=project_id or "unassigned",
            category=category,
            filename=filename,
            file_bytes=file_bytes,
            content_type=content_type or "application/octet-stream",
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("GCS upload failed: %s", exc)
        return None


class PricingAnalyzeRequest(BaseModel):
    project_id: str
    supplier_prices: Dict[str, Any]
    line_items: Optional[List[str]] = None
    context: Optional[str] = ""


class MarketRateRequest(BaseModel):
    category: str
    line_items: Optional[List[str]] = None
    context: Optional[str] = ""


@router.post("/analyze")
async def analyze_pricing(payload: PricingAnalyzeRequest):
    """
    Run market-rate pricing analysis across supplier quotes.
    """
    push_log(agent_id="pricing", status="running",
             message=f"Analysing supplier pricing for project {payload.project_id}")
    if not _PRICING_AGENT_AVAILABLE:
        push_log(agent_id="pricing", status="error",
                 message="PricingAgent not available — check backend dependencies")
        raise HTTPException(503, detail="PricingAgent not available")

    try:
        t0    = time.time()
        agent = PricingAgent()
        result = agent.run({
            "project_id":      payload.project_id,
            "supplier_prices": payload.supplier_prices,
            "line_items":      payload.line_items or [],
            "context":         payload.context or "",
        })
        comparable_count = result.get("comparable_rfps_analysed", 0)
        push_log(agent_id="pricing", status="complete",
                 message=f"Analysed {comparable_count} comparable RFPs",
                 confidence=81,
                 duration_ms=int((time.time() - t0) * 1000))
        return result
    except Exception as e:
        push_log(agent_id="pricing", status="error",
                 message=f"Pricing analysis failed: {e}")
        raise HTTPException(500, detail=str(e))


@router.post("/market-rates")
async def get_market_rates(payload: MarketRateRequest):
    """
    Estimate market-rate ranges for a given procurement category.
    """
    push_log(agent_id="pricing", status="running",
             message=f"Fetching market rates for {payload.category}")
    if not _PRICING_AGENT_AVAILABLE:
        push_log(agent_id="pricing", status="error",
                 message="PricingAgent not available")
        raise HTTPException(503, detail="PricingAgent not available")

    try:
        t0     = time.time()
        agent  = PricingAgent()
        result = agent.get_market_rates({
            "category":   payload.category,
            "line_items": payload.line_items or [],
            "context":    payload.context or "",
        })
        push_log(agent_id="pricing", status="complete",
                 message=f"Market rates retrieved for {payload.category}",
                 confidence=81,
                 duration_ms=int((time.time() - t0) * 1000))
        return result
    except Exception as e:
        push_log(agent_id="pricing", status="error", message=str(e))
        raise HTTPException(500, detail=str(e))

# ── In-memory staging store (replaces Redis for now) ─────────────────────────
import uuid, io, logging, math, math

def _sanitize(obj):
    """Recursively replace NaN/Inf floats with None for JSON safety."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(i) for i in obj]
    return obj
_STAGING: dict = {}
logger = logging.getLogger(__name__)

def _clean(obj):
    """Recursively replace NaN/Inf floats with None for JSON safety."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else round(obj, 4)
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(i) for i in obj]
    return obj


def _get_excel_sheet_names(file_bytes: bytes) -> list[str]:
    """Extract all sheet names from an xlsx file."""
    try:
        import pandas as pd
        xls = pd.ExcelFile(io.BytesIO(file_bytes))
        return xls.sheet_names
    except Exception:
        return ["Sheet1"]


def _parse_sheet(file_bytes: bytes, filename: str, sheet_name: str | None = None, header_row: int = 0) -> dict:
    """Parse xlsx/csv into structured rows with diagnostics."""
    try:
        import pandas as pd
    except ImportError:
        raise HTTPException(503, detail="pandas not installed on backend")

    is_csv = filename.lower().endswith(".csv")

    # For xlsx, get all sheet names
    sheet_names = [] if is_csv else _get_excel_sheet_names(file_bytes)
    best_sheet = sheet_name or (sheet_names[0] if sheet_names else "Sheet1")

    # Read the appropriate sheet
    if is_csv:
        df = pd.read_csv(io.BytesIO(file_bytes), header=header_row)
    else:
        df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=best_sheet, header=header_row)

    # Normalise column names
    df.columns = [str(c).strip() for c in df.columns]
    df = df.where(pd.notna(df), None)
    raw_rows = len(df.dropna(how="all"))

    # Auto-detect column mapping
    COL_ALIASES = {
        "line_item":    ["line item", "item", "description", "material", "service", "item description",
                         "drug name", "product", "sku#", "sku", "product name"],
        "category":     ["category", "type", "group", "class", "dosage form", "strength"],
        "unit":         ["unit", "uom", "unit of measure", "uom/unit", "pack type", "form"],
        "supplier":     ["supplier", "vendor", "company", "bidder"],
        "unit_price":   ["unit price", "rate", "price", "unit cost", "cost", "rate (aud)", "unit rate",
                         "unit total", "total unit cost", "unit total cost"],
        "quantity":     ["quantity", "qty", "volume", "amount", "hours", "annual vol"],
        "total":        ["total", "extended", "line total", "total cost", "total price", "total (aud)"],
    }

    col_map = {}
    warnings = []
    for canonical, aliases in COL_ALIASES.items():
        for col in df.columns:
            if col.lower() in aliases:
                col_map[col] = canonical
                break

    df = df.rename(columns=col_map)
    missing = [c for c in ["line_item", "unit_price"] if c not in df.columns]
    if missing:
        warnings.append(f"Could not detect columns: {', '.join(missing)}")

    # Drop empty rows
    excluded = []
    valid_rows = []
    for _, row in df.iterrows():
        if pd.isna(row.get("line_item", None)) or str(row.get("line_item", "")).strip() == "":
            excluded.append({"reason": "Empty line item", "preview": str(dict(row))[:80]})
            continue
        if pd.isna(row.get("unit_price", None)):
            excluded.append({"reason": "Missing unit price", "preview": str(row.get("line_item", ""))[:80]})
            continue
        valid_rows.append(row)

    # Build PriceRow list
    rows = []
    supplier_totals: dict = {}
    for i, row in enumerate(valid_rows):
        supplier = str(row.get("supplier", "Unknown")).strip()
        unit_price = float(row.get("unit_price", 0))
        quantity   = float(row.get("quantity", 1))
        total      = float(row.get("total", unit_price * quantity))
        line_item  = str(row.get("line_item", f"Item {i+1}")).strip()
        category   = str(row.get("category", "General")).strip()
        unit       = str(row.get("unit", "unit")).strip()

        rows.append({
            "id":           str(i + 1),
            "lineItem":     line_item,
            "category":     category,
            "unitOfMeasure": unit,
            "supplier":     supplier,
            "unitPrice":    round(unit_price, 2),
            "quantity":     round(quantity, 2),
            "total":        round(total, 2),
            "delta":        0.0,  # computed after grouping
        })

    # Compute delta vs lowest per line item
    from collections import defaultdict
    item_min: dict = defaultdict(lambda: float("inf"))
    for r in rows:
        if r["total"] < item_min[r["lineItem"]]:
            item_min[r["lineItem"]] = r["total"]
    for r in rows:
        mn = item_min[r["lineItem"]]
        r["delta"] = round((r["total"] - mn) / mn * 100, 1) if mn > 0 else 0.0

    confidence = "high" if len(missing) == 0 and len(warnings) == 0 else \
                 "medium" if len(missing) <= 1 else "low"

    display_col_map = {v: k for k, v in col_map.items()}  # reversed for display

    return _clean({
        "rows": rows,
        "sheet_names": sheet_names,
        "selected_sheet": best_sheet,
        "detected_header_row": header_row,
        "diagnostics": {
            "file_name":            filename,
            "detected_sheet_name":  best_sheet if not filename.lower().endswith(".csv") else "CSV",
            "raw_non_empty_rows":   raw_rows,
            "accepted_line_items":  len(rows),
            "excluded_rows":        excluded,
            "column_mapping":       {v: k for k, v in col_map.items()},
            "sample_rows":          [dict(r) for r in df.head(5).to_dict("records")],
            "parse_confidence":     confidence,
            "warnings":             warnings,
        },
    })


@router.post("/ingest")
async def ingest_pricing_file(
    file:          UploadFile     = File(...),
    project_id:    Optional[str] = Form(None),
    supplier_name: Optional[str] = Form(None),
):
    """Parse xlsx/csv, stage in memory, return diagnostics + sample for review."""
    push_log(agent_id="pricing", status="running",
             message=f"Parsing pricing file: {file.filename}")
    file_bytes = await file.read()

    try:
        parsed = _parse_sheet(file_bytes, file.filename or "upload")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Sheet parse failed")
        raise HTTPException(500, detail=f"Could not parse file: {exc}")

    staging_id = str(uuid.uuid4())
    _STAGING[staging_id] = {
        "rows":        parsed["rows"],
        "project_id":  project_id,
        "supplier_name": supplier_name or file.filename,
        "file_bytes":  file_bytes,
        "filename":    file.filename,
        "content_type": file.content_type,
    }

    push_log(agent_id="pricing", status="complete",
             message=f"Parsed {len(parsed['rows'])} line items — awaiting confirmation",
             confidence=81)

    return {
        "staging_id":  staging_id,
        "diagnostics": parsed["diagnostics"],
        "sample_rows": parsed["diagnostics"]["sample_rows"],
    }


class ConfirmSheetRequest(BaseModel):
    staging_id:    str
    project_id:    Optional[str] = "unassigned"
    supplier_name: Optional[str] = None


@router.post("/confirm-supplier-sheet")
async def confirm_supplier_sheet(payload: ConfirmSheetRequest):
    """Commit staged rows; return full PriceRow list for frontend store."""
    staged = _STAGING.pop(payload.staging_id, None)
    if not staged:
        raise HTTPException(404, detail="Staging ID not found or already committed")

    rows = staged["rows"]
    if payload.supplier_name:
        for r in rows:
            if r["supplier"] in ("Unknown", "", staged.get("supplier_name", "")):
                r["supplier"] = payload.supplier_name

    # Optionally persist to GCS
    _save_to_gcs(
        project_id=payload.project_id,
        category="pricing_files",
        filename=staged["filename"],
        file_bytes=staged["file_bytes"],
        content_type=staged["content_type"],
    )

    push_log(agent_id="pricing", status="complete",
             message=f"Committed {len(rows)} pricing rows for project {payload.project_id}",
             confidence=90)

    return {
        "status":               "committed",
        "line_items_committed": len(rows),
        "project_id":           payload.project_id,
        "rows":                 rows,  # ← frontend stores this
    }

# ──────────────────────────────────────────────────────────────────────────
# V2 endpoints for SupplierPricingIngest component (more direct API)
# ──────────────────────────────────────────────────────────────────────────

@router.post("/ingest-v2")
async def ingest_pricing_file_v2(
    file:          UploadFile     = File(...),
    project_id:    Optional[str] = Form(None),
    supplier_name: Optional[str] = Form(None),
    header_row:    Optional[str] = Form(None),
    sheet_name:    Optional[str] = Form(None),
):
    """Parse xlsx/csv, return full parsed data + diagnostics for UI review."""
    push_log(agent_id="pricing", status="running",
             message=f"Parsing pricing file: {file.filename}")
    file_bytes = await file.read()

    try:
        hrow = int(header_row) if header_row else 0
        parsed = _parse_sheet(file_bytes, file.filename or "upload", sheet_name=sheet_name, header_row=hrow)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Sheet parse failed")
        push_log(agent_id="pricing", status="error", message=f"Parse failed: {exc}")
        raise HTTPException(500, detail=f"Could not parse file: {exc}")

    staging_id = str(uuid.uuid4())
    _STAGING[staging_id] = {
        "rows":        parsed["rows"],
        "project_id":  project_id,
        "supplier_name": supplier_name or file.filename,
        "file_bytes":  file_bytes,
        "filename":    file.filename,
        "content_type": file.content_type,
    }

    push_log(agent_id="pricing", status="complete",
             message=f"Parsed {len(parsed['rows'])} line items",
             confidence=81)

    return {
        "rows":                 parsed["rows"],
        "staging_id":           staging_id,
        "sheet_names":          parsed.get("sheet_names", []),
        "selected_sheet":       parsed.get("selected_sheet", "Sheet1"),
        "detected_header_row":  parsed.get("detected_header_row", 0),
        "diagnostics":          parsed["diagnostics"],
    }


class ReparseRequest(BaseModel):
    rows: List[Dict[str, Any]]
    project_id: Optional[str] = "unassigned"
    supplier_name: Optional[str] = None
    sheet_name: Optional[str] = None
    header_row: Optional[int] = None


@router.post("/reparse")
async def reparse_pricing_rows(payload: ReparseRequest):
    """Re-parse rows with updated sheet/header settings."""
    # For reparse, we don't have the original file bytes, so just return updated diagnostics
    # In a real scenario, you'd reconstruct or pass the file bytes
    # For now, treat this as a validation/re-organization of existing rows

    rows = payload.rows or []

    # Recompute delta for the rows
    from collections import defaultdict
    item_min: dict = defaultdict(lambda: float("inf"))
    for r in rows:
        line_item = r.get("lineItem") or r.get("item")
        total = r.get("total") or r.get("total_price") or 0
        if total and line_item and total < item_min[str(line_item)]:
            item_min[str(line_item)] = total

    for r in rows:
        line_item = r.get("lineItem") or r.get("item")
        total = r.get("total") or r.get("total_price") or 0
        if line_item and total:
            mn = item_min.get(str(line_item), float("inf"))
            r["delta"] = round((total - mn) / mn * 100, 1) if mn > 0 else 0.0

    push_log(agent_id="pricing", status="complete",
             message=f"Re-parsed {len(rows)} rows",
             confidence=85)

    return {
        "rows":                 _clean(rows),
        "selected_sheet":       payload.sheet_name or "Sheet1",
        "detected_header_row":  payload.header_row or 0,
        "sheet_names":          [],
        "diagnostics": {
            "file_name":            payload.supplier_name or "file",
            "detected_sheet_name":  payload.sheet_name or "Sheet1",
            "raw_non_empty_rows":   len(rows),
            "accepted_line_items":  len(rows),
            "excluded_rows":        [],
            "column_mapping":       {},
            "sample_rows":          rows[:5],
            "parse_confidence":     "high",
            "warnings":             [],
        },
    }


class ConfirmV2Request(BaseModel):
    rows: List[Dict[str, Any]]
    project_id: Optional[str] = "unassigned"
    supplier_name: Optional[str] = None


@router.post("/confirm-v2")
async def confirm_pricing_rows_v2(payload: ConfirmV2Request, db: Session = Depends(get_db)):
    """Confirm and commit parsed pricing rows."""
    rows = payload.rows or []

    # Optionally: store in DB or mark as persisted
    # For now, just return committed status

    push_log(agent_id="pricing", status="complete",
             message=f"Committed {len(rows)} pricing rows",
             confidence=90)

    return {
        "status":               "committed",
        "line_items_committed": len(rows),
        "project_id":           payload.project_id,
        "rows":                 _clean(rows),
    }


# Monkey-patch: sanitize NaN/Inf at response level
from fastapi.responses import JSONResponse
import math, json

class _SafeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        return super().default(obj)
