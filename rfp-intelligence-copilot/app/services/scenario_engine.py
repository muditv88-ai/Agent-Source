"""
scenario_engine.py v2

Handles custom award scenarios from user input (via chatbot or direct API).
The LLM interprets user intent; Python executes the maths.

Supported custom scenario patterns:
  - "award X% to Supplier A, rest to cheapest"
  - "use only Supplier A and Supplier B"
  - "exclude Supplier C"
  - "award category X to Supplier A"
  - "split evenly between Supplier A and Supplier B"
  - "award all items above $500 to Supplier A"
"""
import os
import json
import re
import time
from typing import Any
from openai import OpenAI

_client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)
MODEL = "meta/llama-3.3-70b-instruct"


# ── Intent extraction prompt ─────────────────────────────────────────────────

_SCENARIO_INTENT_PROMPT = """
You are a procurement award scenario interpreter.
The user has described a custom award scenario in natural language.
Extract a structured scenario definition.

Available suppliers: {suppliers}
Available categories: {categories}

Return ONLY this JSON (no extra text):
{{
  "scenario_type": "<one of: percent_split | supplier_subset | exclude_supplier | category_award | even_split | price_threshold | custom>",
  "rules": [
    // One rule per constraint. Each rule is an object.
    // Examples:
    // {{"type": "award_pct",  "supplier": "Acme",  "pct": 60}}
    // {{"type": "award_rest_to_cheapest"}}
    // {{"type": "include_only", "suppliers": ["Acme", "Beta"]}}
    // {{"type": "exclude",      "supplier": "Gamma"}}
    // {{"type": "category_award", "category": "Logistics", "supplier": "Acme"}}
    // {{"type": "even_split",  "suppliers": ["Acme", "Beta"]}}
    // {{"type": "price_threshold", "above": 500, "supplier": "Acme"}}
  ],
  "granularity": "sku"    // or "category"
}}

User request: "{user_input}"
"""


def _call_llm(prompt: str, max_tokens: int = 512, retries: int = 3) -> str:
    delay = 10.0
    for attempt in range(retries + 1):
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
            if ("429" in msg or "rate limit" in msg) and attempt < retries:
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


# ── Intent extraction ─────────────────────────────────────────────────────────

def parse_scenario_intent(user_input: str, suppliers: list, categories: list) -> dict:
    """
    Ask LLM to parse user's natural-language scenario into a structured rule set.
    Returns parsed intent dict or raises ValueError.
    """
    prompt = _SCENARIO_INTENT_PROMPT.format(
        suppliers=", ".join(suppliers),
        categories=", ".join(categories),
        user_input=user_input,
    )
    raw    = _call_llm(prompt, max_tokens=512)
    intent = _parse_json(raw)
    if "rules" not in intent:
        raise ValueError(f"LLM returned malformed intent: {raw[:200]}")
    return intent


# ── Rule executors ────────────────────────────────────────────────────────────

def _execute_sku_scenario(rules: list, cost_model: dict) -> dict:
    """
    Execute a custom scenario at SKU level.
    Returns breakdown list and grand total.
    """
    matrix    = cost_model["matrix"]
    suppliers = cost_model["suppliers"]

    # Pre-process rules
    include_only = None
    excluded     = set()
    cat_overrides: dict[str, str] = {}   # {category: supplier}
    pct_awards: list[dict] = []
    price_threshold: dict  = {}
    even_split_suppliers: list = []

    for rule in rules:
        t = rule.get("type")
        if t == "include_only":
            include_only = set(rule.get("suppliers", []))
        elif t == "exclude":
            excluded.add(rule.get("supplier", ""))
        elif t == "category_award":
            cat_overrides[rule.get("category", "")] = rule.get("supplier", "")
        elif t == "award_pct":
            pct_awards.append(rule)
        elif t == "price_threshold":
            price_threshold = rule
        elif t == "even_split":
            even_split_suppliers = rule.get("suppliers", [])

    active_suppliers = [
        s for s in suppliers
        if s not in excluded
        and (include_only is None or s in include_only)
    ]
    if not active_suppliers:
        active_suppliers = suppliers  # fallback to all

    breakdown   = []
    grand_total = 0.0
    award_split: dict[str, float] = {s: 0.0 for s in active_suppliers}

    for desc, smap in matrix.items():
        priced = {
            s: smap[s] for s in active_suppliers
            if smap.get(s) and smap[s]["total"] > 0
        }
        if not priced:
            continue

        cat = next((v["category"] for v in priced.values() if v), "")

        # Category override
        if cat in cat_overrides and cat_overrides[cat] in priced:
            awarded_to   = cat_overrides[cat]
            awarded_val  = priced[awarded_to]
            award_reason = f"Category rule: {cat} → {awarded_to}"

        # Even split — alternate suppliers round-robin (use min-cost of pair)
        elif even_split_suppliers:
            eligible = [s for s in even_split_suppliers if s in priced]
            if eligible:
                awarded_to   = min(eligible, key=lambda s: priced[s]["total"])
                awarded_val  = priced[awarded_to]
                award_reason = f"Even split (cheapest of {eligible})"
            else:
                awarded_to   = min(priced, key=lambda s: priced[s]["total"])
                awarded_val  = priced[awarded_to]
                award_reason = "Even split fallback (cheapest)"

        # Price threshold — items above threshold go to named supplier
        elif price_threshold:
            threshold_val = float(price_threshold.get("above", 0))
            threshold_sup = price_threshold.get("supplier", "")
            first_val     = next(iter(priced.values()))
            if first_val["total"] > threshold_val and threshold_sup in priced:
                awarded_to   = threshold_sup
                awarded_val  = priced[threshold_sup]
                award_reason = f"Above {threshold_val} threshold → {threshold_sup}"
            else:
                awarded_to   = min(priced, key=lambda s: priced[s]["total"])
                awarded_val  = priced[awarded_to]
                award_reason = "Below threshold — cheapest"

        # Default: cheapest
        else:
            awarded_to   = min(priced, key=lambda s: priced[s]["total"])
            awarded_val  = priced[awarded_to]
            award_reason = "Cheapest"

        grand_total += awarded_val["total"]
        award_split[awarded_to] = round(award_split.get(awarded_to, 0.0) + awarded_val["total"], 2)

        breakdown.append({
            "description":   desc,
            "category":      cat,
            "awarded_to":    awarded_to,
            "awarded_total": awarded_val["total"],
            "unit_price":    awarded_val["unit_price"],
            "quantity":      awarded_val["quantity"],
            "all_prices":    {s: (priced[s]["total"] if s in priced else None) for s in active_suppliers},
            "award_reason":  award_reason,
        })

    return {
        "breakdown":        breakdown,
        "grand_total":      round(grand_total, 2),
        "award_split":      award_split,
        "active_suppliers": active_suppliers,
    }


def _execute_category_scenario(rules: list, cost_model: dict) -> dict:
    """Execute a custom scenario at category level."""
    cat_matrix = cost_model["category_matrix"]
    suppliers  = cost_model["suppliers"]

    include_only = None
    excluded     = set()
    cat_overrides: dict[str, str] = {}

    for rule in rules:
        t = rule.get("type")
        if t == "include_only":
            include_only = set(rule.get("suppliers", []))
        elif t == "exclude":
            excluded.add(rule.get("supplier", ""))
        elif t == "category_award":
            cat_overrides[rule.get("category", "")] = rule.get("supplier", "")

    active_suppliers = [
        s for s in suppliers
        if s not in excluded and (include_only is None or s in include_only)
    ]
    if not active_suppliers:
        active_suppliers = suppliers

    allocation  = {}
    grand_total = 0.0
    award_split: dict[str, float] = {s: 0.0 for s in active_suppliers}

    for cat, smap in cat_matrix.items():
        active_smap = {s: smap[s] for s in active_suppliers if s in smap}
        if not active_smap:
            continue

        if cat in cat_overrides and cat_overrides[cat] in active_smap:
            best_s    = cat_overrides[cat]
            best_cost = active_smap[best_s]
            reason    = f"Rule: award {cat} to {best_s}"
        else:
            best_s    = min(active_smap, key=lambda s: active_smap[s])
            best_cost = active_smap[best_s]
            reason    = "Cheapest supplier in category"

        grand_total += best_cost
        award_split[best_s] = round(award_split.get(best_s, 0.0) + best_cost, 2)
        allocation[cat] = {
            "awarded_to": best_s,
            "cost":       round(best_cost, 2),
            "all_costs":  {s: round(active_smap.get(s, 0), 2) for s in active_suppliers},
            "reason":     reason,
        }

    return {
        "allocation":       allocation,
        "grand_total":      round(grand_total, 2),
        "award_split":      award_split,
        "active_suppliers": active_suppliers,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def run_custom_scenario(
    user_input: str,
    cost_model: dict,
    scenario_id: str = None,
) -> dict:
    """
    Full pipeline: parse user intent → execute Python maths → return result.

    Args:
        user_input : natural-language scenario description
        cost_model : from pricing_analyzer.build_cost_model()
        scenario_id: optional ID for storage

    Returns:
        scenario result dict including intent, breakdown, totals, award_split
    """
    import uuid
    suppliers  = cost_model.get("suppliers", [])
    categories = list(cost_model.get("category_matrix", {}).keys())

    intent = parse_scenario_intent(user_input, suppliers, categories)
    granularity = intent.get("granularity", "sku")
    rules       = intent.get("rules", [])

    if granularity == "category":
        result = _execute_category_scenario(rules, cost_model)
    else:
        result = _execute_sku_scenario(rules, cost_model)

    return {
        "scenario_id":   scenario_id or str(uuid.uuid4())[:8],
        "scenario_type": "custom",
        "user_input":    user_input,
        "intent":        intent,
        "granularity":   granularity,
        "total_cost":    result["grand_total"],
        "award_split":   result.get("award_split", {}),
        "breakdown":     result.get("breakdown"),       # SKU mode
        "allocation":    result.get("allocation"),      # category mode
        "active_suppliers": result.get("active_suppliers", []),
        "description":   f"Custom: {user_input[:120]}",
    }
