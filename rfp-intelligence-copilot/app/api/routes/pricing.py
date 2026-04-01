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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents.pricing_agent import PricingAgent

router = APIRouter(prefix="/pricing", tags=["Pricing Analysis"])


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


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/analyze")
async def analyze_pricing(payload: AnalyzePricingRequest):
    """
    FM-7.1 / FM-7.2 — Full pipeline:
      1. Parse each supplier's pricing sheet
      2. Normalize UoMs
      3. Build cost model
      4. Run comparative analysis
    Returns cost_model, analysis summary, and normalized_pricing.
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
    FM-7.5 — Flag expired or near-expiry (≤30 days) quotes.
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

        # Build a flat comparison table from cost_model
        comparison = []
        cost_model = result.get("cost_model", {})
        for supplier, data in cost_model.items() if isinstance(cost_model, dict) else []:
            comparison.append({
                "supplier": supplier,
                "total_cost": data.get("total_cost"),
                "unit_price": data.get("unit_price"),
                "currency": payload.base_currency,
                "line_item_count": data.get("line_item_count"),
            })

        # Sort by total_cost ascending (cheapest first)
        comparison.sort(key=lambda x: (x.get("total_cost") or float("inf")))

        return {
            "project_id": payload.project_id,
            "base_currency": payload.base_currency,
            "ranked_suppliers": comparison,
            "full_analysis": result.get("analysis"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
