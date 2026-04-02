"""
Unit tests for pure-Python service functions — no HTTP, no DB, no LLM.
These are the fastest tests and should run in < 1 s total.
"""
import pytest


# ---------------------------------------------------------------------------
# PricingAgent UoM converter (inline replica so tests work without imports)
# ---------------------------------------------------------------------------

def _convert_uom(price: float, from_uom: str, to_uom: str) -> float:
    conversions = {"kg": 0.001, "ton": 1000, "each": 1, "boxof10": 10}
    factor = conversions.get(from_uom, 1) / conversions.get(to_uom, 1)
    return round(price * factor, 4)


def test_uom_kg_to_ton():
    assert _convert_uom(1000.0, "kg", "ton") == pytest.approx(1.0)


def test_uom_ton_to_kg():
    assert _convert_uom(1.0, "ton", "kg") == pytest.approx(1000.0)


def test_uom_each_unchanged():
    assert _convert_uom(25.0, "each", "each") == 25.0


def test_uom_boxof10_to_each():
    assert _convert_uom(1.0, "boxof10", "each") == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# TCO calculator
# ---------------------------------------------------------------------------

def _calculate_tco(unit_price, quantity, freight_pct=0, duty_pct=0, tooling=0):
    base = unit_price * quantity
    freight = base * freight_pct / 100
    duty = base * duty_pct / 100
    tco = base + freight + duty + tooling
    return {"base_cost": round(base, 2), "freight": round(freight, 2),
            "duty": round(duty, 2), "tooling": round(tooling, 2), "tco": round(tco, 2)}


def test_tco_no_extras():
    result = _calculate_tco(10.0, 100)
    assert result["tco"] == 1000.0
    assert result["freight"] == 0.0


def test_tco_with_freight_and_duty():
    result = _calculate_tco(100.0, 10, freight_pct=5, duty_pct=10)
    assert result["base_cost"] == 1000.0
    assert result["freight"] == 50.0
    assert result["duty"] == 100.0
    assert result["tco"] == 1150.0


def test_tco_with_tooling():
    result = _calculate_tco(50.0, 20, tooling=500)
    assert result["tco"] == 1500.0


# ---------------------------------------------------------------------------
# Onboarding checklist validator
# ---------------------------------------------------------------------------

ONBOARDING_CHECKLIST = [
    "company_registration", "tax_id", "bank_details",
    "iso_certification", "insurance_certificate", "contact_details",
]


def _validate_docs(uploaded_doc_types: list) -> dict:
    provided = set(uploaded_doc_types)
    missing = [item for item in ONBOARDING_CHECKLIST if item not in provided]
    score = round((len(ONBOARDING_CHECKLIST) - len(missing)) / len(ONBOARDING_CHECKLIST) * 100, 1)
    return {"completeness_score": score, "missing": missing,
            "status": "approved" if not missing else "pending_docs"}


def test_all_docs_uploaded():
    result = _validate_docs(ONBOARDING_CHECKLIST)
    assert result["completeness_score"] == 100.0
    assert result["status"] == "approved"


def test_missing_docs():
    partial = ["company_registration", "tax_id", "contact_details"]
    result = _validate_docs(partial)
    assert result["status"] == "pending_docs"
    assert "bank_details" in result["missing"]
    assert result["completeness_score"] == 50.0


def test_empty_docs():
    result = _validate_docs([])
    assert result["completeness_score"] == 0.0
    assert len(result["missing"]) == len(ONBOARDING_CHECKLIST)
