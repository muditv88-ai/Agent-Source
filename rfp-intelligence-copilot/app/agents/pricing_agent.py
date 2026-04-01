"""
pricing_agent.py

Handles FM-7.1 through FM-7.6:
  - Parse + normalize pricing sheets (UoM normalization)
  - TCO calculation (unit + freight + duty + tooling)
  - Currency normalization (live FX rates)
  - Price validity tracking (expired / expiring soon)

Wraps existing pricing_analyzer.py and pricing_parser.py — unchanged.
"""
import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from app.agents.base_agent import BaseAgent, Tool
from app.services.pricing_analyzer import build_cost_model, analyze_pricing
from app.services.pricing_parser import parse_pricing_response

logger = logging.getLogger(__name__)

# Unit-of-measure conversion factors (to base unit)
UOM_FACTORS: Dict[str, float] = {
    "each": 1.0,
    "unit": 1.0,
    "pcs": 1.0,
    "kg": 1.0,
    "g": 0.001,
    "ton": 1000.0,
    "mt": 1000.0,
    "l": 1.0,
    "ml": 0.001,
    "m": 1.0,
    "cm": 0.01,
    "mm": 0.001,
    "box_of_10": 10.0,
    "box_of_100": 100.0,
    "dozen": 12.0,
    "pair": 2.0,
    "set": 1.0,
    "hour": 1.0,
    "day": 8.0,
}


class PricingAgent(BaseAgent):
    """
    Enriches the existing pricing services with:
      - UoM normalisation across suppliers
      - Total Cost of Ownership (TCO)
      - Currency conversion
      - Quote validity monitoring
    """

    def __init__(self, base_currency: str = "USD"):
        self.base_currency = base_currency

        tools = [
            Tool(
                name="parse_and_normalize",
                description="Parse pricing sheets and normalize units of measure",
                fn=self._parse_normalize,
                schema={"type": "object"},
            ),
            Tool(
                name="calculate_tco",
                description="Calculate Total Cost of Ownership including freight, duty and tooling",
                fn=self._calculate_tco,
                schema={"type": "object"},
            ),
            Tool(
                name="normalize_currency",
                description="Convert all prices to a common base currency using live FX rates",
                fn=self._normalize_currency,
                schema={"type": "object"},
            ),
            Tool(
                name="check_price_validity",
                description="Flag expired or near-expiry supplier quotes",
                fn=self._check_validity,
                schema={"type": "object"},
            ),
        ]
        super().__init__(tools)

    def _convert_uom(self, price: float, from_uom: str, to_uom: str = "each") -> float:
        """Convert a unit price between two UoM standards."""
        from_factor = UOM_FACTORS.get(from_uom.lower().strip(), 1.0)
        to_factor = UOM_FACTORS.get(to_uom.lower().strip(), 1.0)
        return round(price * from_factor / to_factor, 6)

    def _parse_normalize(self, raw_pricing_data: list) -> dict:
        """
        raw_pricing_data: list of {supplier, file_text, uom (optional)}
        Returns normalized pricing per supplier.
        """
        normalized = []
        for item in raw_pricing_data:
            try:
                parsed = parse_pricing_response(item.get("file_text", ""))
                for line in parsed.get("line_items", []):
                    uom = line.get("unit", "each")
                    unit_price = line.get("unit_price") or line.get("price", 0)
                    line["normalized_unit_price"] = self._convert_uom(
                        float(unit_price), uom
                    )
                    line["base_uom"] = "each"
                normalized.append({"supplier": item.get("supplier"), "data": parsed})
            except Exception as e:
                logger.warning("parse_normalize failed for %s: %s", item.get("supplier"), e)
        return {"normalized_pricing": normalized}

    def _calculate_tco(
        self,
        unit_price: float,
        quantity: int,
        freight_pct: float = 0.0,
        duty_pct: float = 0.0,
        tooling_cost: float = 0.0,
    ) -> dict:
        base = unit_price * quantity
        freight = base * (freight_pct / 100)
        duty = base * (duty_pct / 100)
        tco = base + freight + duty + tooling_cost
        return {
            "base_cost": round(base, 2),
            "freight": round(freight, 2),
            "duty": round(duty, 2),
            "tooling": round(tooling_cost, 2),
            "tco": round(tco, 2),
        }

    def _normalize_currency(self, prices: list, source_currency: str = "") -> list:
        """Convert prices to base_currency using live exchangerate.host API."""
        try:
            import requests

            rates = (
                requests.get(
                    f"https://api.exchangerate.host/latest?base={self.base_currency}",
                    timeout=5,
                )
                .json()
                .get("rates", {})
            )
        except Exception:
            rates = {}
            logger.warning("FX rate fetch failed — using 1:1 fallback")

        for item in prices:
            src = item.get("currency", source_currency or self.base_currency)
            rate = rates.get(src, 1.0)
            item["price_normalized"] = round(item.get("price", 0) / rate, 4)
            item["base_currency"] = self.base_currency
        return prices

    def _check_validity(self, quotes: list) -> list:
        today = datetime.utcnow().date()
        for q in quotes:
            expiry = q.get("valid_until")
            if expiry:
                try:
                    exp_date = date.fromisoformat(str(expiry))
                    delta = (exp_date - today).days
                    q["validity_status"] = (
                        "expired"
                        if delta < 0
                        else "expiring_soon"
                        if delta <= 30
                        else "valid"
                    )
                    q["days_to_expiry"] = delta
                except ValueError:
                    q["validity_status"] = "unknown"
        return quotes

    def run(self, input: dict, context: Optional[Dict] = None) -> dict:
        normalized = self._parse_normalize(input.get("raw_pricing_data", []))

        try:
            cost_model = build_cost_model(normalized["normalized_pricing"])
            analysis = analyze_pricing(cost_model)
        except Exception as e:
            logger.error("Pricing analysis failed: %s", e)
            cost_model = {}
            analysis = {}

        return {
            "cost_model": cost_model,
            "analysis": analysis,
            "normalized_pricing": normalized,
        }
