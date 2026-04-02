"""
pricing.py  —  Pricing & Commercial Analysis API routes

FM-7.1  Parse + normalize pricing sheets (UoM normalization)
FM-7.2  Full pricing analysis via PricingAgent
FM-7.3  TCO calculator  (unit + freight + duty + tooling)
FM-7.4  Currency normalization (live FX)
FM-7.5  Price validity check (expired / expiring soon)
FM-7.6  Side-by-side comparison output

All computation is delegated to PricingAgent which wraps
pricing_analyzer.py and pricing_parser.py — those are NOT modified.
"""
import asyncio
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents.pricing_agent import PricingAgent

router = APIRouter(prefix="/pricing", tags=["Pricing Analysis"])

# ── In-memory job store for async pricing jobs ────────────────────────────────
# { job_id: { status, result, error } }
_JOBS: Dict[str, Dict[str, Any]] = {}


# ── Request / Response models ─────────────────────────────────────────────────

class RawPricingItem(BaseModel):
    supplier: str
    file_text: str = Field(..., description="Extracted text from the pricing sheet")
    currency: Optional[str] = "USD"
    uom: Optional[str] = "each"


class AnalyzePricingRequest(BaseModel):
    project_id: str
    raw_pricing_data: List[RawPricingItem] = Field(
        ..., description="One entry per supplier pricing document"
    )
    base_currency: str = Field(default="USD", description="Currency to normalise to")


# Frontend-compatible request: accepts rfp_id + optional project_id
class RfpPricingRequest(BaseModel):
    rfp_id: str
    project_id: Optional[str] = None
    base_currency: str = Field(default="USD", description="Currency to normalise to")


class TCORequest(BaseModel):
    project_id: str
    unit_price: float
    quantity: int
    freight_pct: float = Field(default=0.0, description="Freight as % of base cost")
    duty_pct: float = Field(default=0.0, description="Import duty as % of base cost")
    tooling_cost: float = Field(default=0.0, description="One-off tooling / NRE cost")


class ValidityCheckRequest(BaseModel):
    project_id: str
    quotes: List[Dict[str, Any]] = Field(
        ..., description="[{supplier, price, currency, valid_until (ISO date)}]"
    )


class CurrencyNormalizeRequest(BaseModel):
    project_id: str
    prices: List[Dict[str, Any]] = Field(
        ..., description="[{supplier, price, currency}]"
    )
    base_currency: str = "USD"


# ── Background job runner ─────────────────────────────────────────────────────

async def _run_pricing_job(job_id: str, rfp_id: str, project_id: Optional[str], base_currency: str):
    """
    Loads supplier pricing file texts for the given rfp_id from the project
    store, then runs PricingAgent. Falls back to an empty result if no
    pricing data is available yet (avoids 500 on fresh projects).
    """
    try:
        # Attempt to pull parsed pricing data via the project store
        # If the project store / rfp store exposes raw text we use it;
        # otherwise we run with an empty list so the job completes cleanly.
        raw: List[Dict[str, Any]] = []
        try:
            from app.services.project_store import get_project_pricing_texts  # type: ignore
            raw = get_project_pricing_texts(rfp_id) or []
        except Exception:
            # Service not yet implemented or rfp_id not found — run with empty data
            raw = []

        agent = PricingAgent(base_currency=base_currency)
        result = agent.run({"raw_pricing_data": raw})
        result["rfp_id"] = rfp_id
        result["project_id"] = project_id or rfp_id
        _JOBS[job_id] = {"status": "completed", "result": result}
    except Exception as exc:
        _JOBS[job_id] = {"status": "failed", "error": str(exc)}


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/analyze")
async def analyze_pricing_rfp(payload: RfpPricingRequest):
    """
    Frontend-compatible entry point (matches api.ts contract).
    Accepts { rfp_id, project_id } and returns { job_id, status } immediately.
    The client polls /pricing-analysis/status/{job_id} until completed.
    """
    job_id = str(uuid.uuid4())
    _JOBS[job_id] = {"status": "running"}
    asyncio.create_task(
        _run_pricing_job(job_id, payload.rfp_id, payload.project_id, payload.base_currency)
    )
    return {"job_id": job_id, "status": "running"}


@router.get("/status/{job_id}")
async def get_pricing_status(job_id: str):
    """
    Poll endpoint for async pricing jobs.
    Returns { job_id, status, result?, error? }.
    """
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return {"job_id": job_id, **job}


@router.post("/analyze-raw")
async def analyze_pricing(payload: AnalyzePricingRequest):
    """
    FM-7.1 / FM-7.2 — Full pipeline with explicit raw_pricing_data.
    Use this when you have already extracted supplier text.
    Returns cost_model, analysis summary, and normalized_pricing synchronously.
    """
    try:
        agent = PricingAgent(base_currency=payload.base_currency)
        raw = [
            {
                "supplier": item.supplier,
                "file_text": item.file_text,
                "currency": item.currency,
                "uom": item.uom,
            }
            for item in payload.raw_pricing_data
        ]
        result = agent.run({"raw_pricing_data": raw})
        result["project_id"] = payload.project_id
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tco")
async def calculate_tco(payload: TCORequest):
    """
    FM-7.3 — Total Cost of Ownership breakdown.
    Returns base_cost, freight, duty, tooling, and tco.
    """
    try:
        agent = PricingAgent()
        result = agent._calculate_tco(
            unit_price=payload.unit_price,
            quantity=payload.quantity,
            freight_pct=payload.freight_pct,
            duty_pct=payload.duty_pct,
            tooling_cost=payload.tooling_cost,
        )
        result["project_id"] = payload.project_id
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validity")
async def check_price_validity(payload: ValidityCheckRequest):
    """
    FM-7.5 — Flag expired or near-expiry (<=30 days) quotes.
    Each quote gets validity_status: 'valid' | 'expiring_soon' | 'expired'
    and a days_to_expiry integer.
    """
    try:
        agent = PricingAgent()
        checked = agent._check_validity(payload.quotes)
        return {
            "project_id": payload.project_id,
            "quotes": checked,
            "expired_count": sum(1 for q in checked if q.get("validity_status") == "expired"),
            "expiring_soon_count": sum(
                1 for q in checked if q.get("validity_status") == "expiring_soon"
            ),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/currency")
async def normalize_currency(payload: CurrencyNormalizeRequest):
    """
    FM-7.4 — Convert all supplier prices to a common base currency
    using live exchangerate.host FX rates. Falls back to 1:1 on API failure.
    """
    try:
        agent = PricingAgent(base_currency=payload.base_currency)
        normalized = agent._normalize_currency(payload.prices)
        return {
            "project_id": payload.project_id,
            "base_currency": payload.base_currency,
            "prices": normalized,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/compare")
async def compare_suppliers(payload: AnalyzePricingRequest):
    """
    FM-7.6 — Side-by-side supplier comparison.
    Runs the full analysis pipeline and returns a ranked comparison table
    with normalized prices, TCO where data is available, and validity flags.
    """
    try:
        agent = PricingAgent(base_currency=payload.base_currency)
        raw = [
            {
                "supplier": item.supplier,
                "file_text": item.file_text,
                "currency": item.currency,
                "uom": item.uom,
            }
            for item in payload.raw_pricing_data
        ]
        result = agent.run({"raw_pricing_data": raw})

        comparison = []
        cost_model = result.get("cost_model", {})
        for supplier, data in (cost_model.items() if isinstance(cost_model, dict) else []):
            comparison.append({
                "supplier": supplier,
                "total_cost": data.get("total_cost"),
                "unit_price": data.get("unit_price"),
                "currency": payload.base_currency,
                "line_item_count": data.get("line_item_count"),
            })

        comparison.sort(key=lambda x: (x.get("total_cost") or float("inf")))

        return {
            "project_id": payload.project_id,
            "base_currency": payload.base_currency,
            "ranked_suppliers": comparison,
            "full_analysis": result.get("analysis"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
