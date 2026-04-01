"""
pricing_analyzer.py v2.1

All maths done in pure Python (no LLM).

Scenarios:
  1. Total cost per supplier  (L1 / L2 / L3 ranking)
  2. Best of Best             (cheapest per SKU/line-item, any supplier)
  3. Optimised Award — per SKU   (min total cost, award each item independently)
  4. Optimised Award — per Category (award entire category to cheapest supplier)
  5. Market Basket (2-supplier split, both per-SKU and per-category)
  6. Market Basket (3-supplier split, both per-SKU and per-category)
  7. Award recommendation    (compare all scenarios, recommend best risk-adjusted)

Data contract:
  Input : list of extract_pricing_from_document() dicts
  Output: dict ready to be JSON-serialised and stored

v2.1: added analyze_pricing as an alias for run_pricing_analysis so that
      pricing_agent.py can import either name without error.
"""
from itertools import combinations
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# Cost model
# ═══════════════════════════════════════════════════════════════════════════════

def build_cost_model(suppliers_pricing: list[dict]) -> dict:
    """
    Build a unified cost model.

    Returns:
        suppliers      : list of supplier names
        descriptions   : ordered list of all SKU/line-item descriptions
        matrix         : {description: {supplier: {unit_price, quantity, total, category, unit, sku, notes}}}
        category_matrix: {category: {supplier: total_cost}}
    """
    suppliers = [sp["supplier_name"] for sp in suppliers_pricing]

    # Collect all unique descriptions — preserve first-seen order
    seen, all_descs = set(), []
    for sp in suppliers_pricing:
        for item in sp.get("all_line_items", []):
            d = item["description"].strip()
            if d and d not in seen:
                all_descs.append(d)
                seen.add(d)

    # Build SKU-level matrix
    matrix: dict[str, dict] = {}
    for desc in all_descs:
        matrix[desc] = {}
        for sp in suppliers_pricing:
            sname = sp["supplier_name"]
            match = next(
                (i for i in sp.get("all_line_items", []) if i["description"].strip() == desc),
                None,
            )
            if match and match["total"] > 0:
                matrix[desc][sname] = {
                    "sku":        match.get("sku", ""),
                    "unit_price": match["unit_price"],
                    "quantity":   match["quantity"],
                    "total":      match["total"],
                    "category":   match["category"],
                    "unit":       match.get("unit", "each"),
                    "notes":      match.get("notes", ""),
                }
            else:
                matrix[desc][sname] = None

    # Build category-level aggregation
    cat_matrix: dict[str, dict[str, float]] = {}
    for sp in suppliers_pricing:
        sname = sp["supplier_name"]
        for item in sp.get("all_line_items", []):
            cat = (item.get("category") or "Uncategorised").strip()
            cat_matrix.setdefault(cat, {})
            cat_matrix[cat][sname] = round(
                cat_matrix[cat].get(sname, 0.0) + item["total"], 2
            )

    return {
        "suppliers":       suppliers,
        "descriptions":    all_descs,
        "matrix":          matrix,
        "category_matrix": cat_matrix,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Scenario 1 — Total cost / L1-L2-L3 ranking
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_total_cost(suppliers_pricing: list[dict]) -> list[dict]:
    results = []
    for sp in suppliers_pricing:
        by_cat: dict[str, float] = {}
        for item in sp.get("all_line_items", []):
            cat = (item.get("category") or "Uncategorised").strip()
            by_cat[cat] = round(by_cat.get(cat, 0.0) + item["total"], 2)
        results.append({
            "supplier_name":   sp["supplier_name"],
            "total_cost":      round(sp.get("total_cost", 0.0), 2),
            "by_category":     by_cat,
            "line_item_count": len(sp.get("all_line_items", [])),
        })
    results.sort(key=lambda x: x["total_cost"])
    for i, r in enumerate(results):
        r["rank"]  = i + 1
        r["label"] = f"L{i + 1}"  # L1 = cheapest
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Scenario 2 — Best of Best (per SKU, any supplier)
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_best_of_best(cost_model: dict) -> dict:
    """
    For each line item, pick the supplier with the lowest total.
    This is the theoretical floor — achievable only by splitting every item.
    """
    matrix      = cost_model["matrix"]
    suppliers   = cost_model["suppliers"]
    breakdown   = []
    grand_total = 0.0
    wins: dict[str, int] = {s: 0 for s in suppliers}

    for desc, smap in matrix.items():
        priced = {s: v for s, v in smap.items() if v is not None and v["total"] > 0}
        if not priced:
            continue
        best_s   = min(priced, key=lambda s: priced[s]["total"])
        best_val = priced[best_s]
        grand_total += best_val["total"]
        wins[best_s] = wins.get(best_s, 0) + 1

        breakdown.append({
            "description":     desc,
            "sku":             best_val.get("sku", ""),
            "category":        best_val["category"],
            "best_supplier":   best_s,
            "best_unit_price": best_val["unit_price"],
            "best_total":      best_val["total"],
            "quantity":        best_val["quantity"],
            "unit":            best_val.get("unit", "each"),
            "all_prices":      {s: (v["total"] if v else None) for s, v in smap.items()},
            "all_unit_prices": {s: (v["unit_price"] if v else None) for s, v in smap.items()},
            "savings_vs_worst": _savings_vs_worst(priced),
        })

    return {
        "scenario":        "best_of_best",
        "total_cost":      round(grand_total, 2),
        "breakdown":       breakdown,
        "wins_by_supplier": wins,
        "description":     "Lowest price per SKU across all suppliers — theoretical minimum",
    }


def _savings_vs_worst(priced: dict) -> float:
    if len(priced) < 2:
        return 0.0
    vals = [v["total"] for v in priced.values()]
    return round(max(vals) - min(vals), 2)


# ═══════════════════════════════════════════════════════════════════════════════
# Scenario 3 — Optimised Award (two modes)
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_optimised_award_sku(cost_model: dict) -> dict:
    """
    Per-SKU optimised award: award each line item to cheapest supplier.
    Identical to Best-of-Best but explicitly labelled as an award scenario
    and includes supplier consolidation analysis.
    """
    bob = scenario_best_of_best(cost_model)
    suppliers_used = {b["best_supplier"] for b in bob["breakdown"]}
    award_split = {}
    for b in bob["breakdown"]:
        s = b["best_supplier"]
        award_split[s] = award_split.get(s, 0.0) + b["best_total"]

    return {
        "scenario":         "optimised_award_sku",
        "total_cost":       bob["total_cost"],
        "breakdown":        bob["breakdown"],
        "suppliers_used":   sorted(suppliers_used),
        "award_split":      {s: round(v, 2) for s, v in award_split.items()},
        "supplier_count":   len(suppliers_used),
        "description":      "Award each SKU to cheapest supplier — maximum savings, higher complexity",
    }


def scenario_optimised_award_category(cost_model: dict) -> dict:
    """
    Per-category optimised award: award each category to cheapest supplier.
    Lower complexity than per-SKU (fewer POs, easier to manage).
    """
    cat_matrix  = cost_model["category_matrix"]
    suppliers   = cost_model["suppliers"]
    allocation  = {}   # {category: {supplier, cost}}
    grand_total = 0.0
    award_split: dict[str, float] = {}

    for cat, smap in cat_matrix.items():
        if not smap:
            continue
        best_s    = min(smap, key=lambda s: smap.get(s, float("inf")))
        best_cost = smap[best_s]
        grand_total += best_cost
        allocation[cat] = {
            "awarded_to": best_s,
            "cost":       round(best_cost, 2),
            "all_costs":  {s: round(smap.get(s, 0), 2) for s in suppliers},
        }
        award_split[best_s] = round(award_split.get(best_s, 0.0) + best_cost, 2)

    suppliers_used = set(v["awarded_to"] for v in allocation.values())
    return {
        "scenario":        "optimised_award_category",
        "total_cost":      round(grand_total, 2),
        "allocation":      allocation,
        "suppliers_used":  sorted(suppliers_used),
        "award_split":     award_split,
        "supplier_count":  len(suppliers_used),
        "description":     "Award each category to cheapest supplier — balanced savings and simplicity",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Scenario 4 — Market Basket (n-supplier splits)
# ═══════════════════════════════════════════════════════════════════════════════

def _market_basket_sku(cost_model: dict, n: int) -> list[dict]:
    """Per-SKU market basket: best price per item within a fixed n-supplier combo."""
    matrix    = cost_model["matrix"]
    suppliers = cost_model["suppliers"]
    if len(suppliers) < n:
        return []
    results = []
    for combo in combinations(suppliers, n):
        total = 0.0
        breakdown = []
        for desc, smap in matrix.items():
            priced = {s: smap[s] for s in combo if smap.get(s) and smap[s]["total"] > 0}
            if not priced:
                continue
            best_s   = min(priced, key=lambda s: priced[s]["total"])
            best_val = priced[best_s]
            total   += best_val["total"]
            breakdown.append({
                "description":   desc,
                "best_supplier": best_s,
                "best_total":    best_val["total"],
                "all_prices":    {s: (priced[s]["total"] if s in priced else None) for s in combo},
            })
        award_split = {}
        for b in breakdown:
            award_split[b["best_supplier"]] = round(
                award_split.get(b["best_supplier"], 0.0) + b["best_total"], 2
            )
        results.append({
            "suppliers":   list(combo),
            "total_cost":  round(total, 2),
            "breakdown":   breakdown,
            "award_split": award_split,
            "mode":        "per_sku",
        })
    results.sort(key=lambda x: x["total_cost"])
    return results


def _market_basket_category(cost_model: dict, n: int) -> list[dict]:
    """Per-category market basket: best category cost within a fixed n-supplier combo."""
    cat_matrix = cost_model["category_matrix"]
    suppliers  = cost_model["suppliers"]
    if len(suppliers) < n:
        return []
    results = []
    for combo in combinations(suppliers, n):
        total      = 0.0
        allocation = {}
        for cat, smap in cat_matrix.items():
            combo_costs = {s: smap.get(s, float("inf")) for s in combo}
            best_s      = min(combo_costs, key=lambda s: combo_costs[s])
            best_cost   = combo_costs[best_s]
            if best_cost == float("inf"):
                continue
            total          += best_cost
            allocation[cat] = {
                "awarded_to": best_s,
                "cost":       round(best_cost, 2),
                "all_costs":  {s: round(combo_costs[s], 2) for s in combo
                               if combo_costs[s] != float("inf")},
            }
        award_split = {}
        for cat, detail in allocation.items():
            s = detail["awarded_to"]
            award_split[s] = round(award_split.get(s, 0.0) + detail["cost"], 2)
        results.append({
            "suppliers":   list(combo),
            "total_cost":  round(total, 2),
            "allocation":  allocation,
            "award_split": award_split,
            "mode":        "per_category",
        })
    results.sort(key=lambda x: x["total_cost"])
    return results


def scenario_market_basket(cost_model: dict, n: int) -> dict:
    """Run both per-SKU and per-category market basket for n suppliers."""
    sku_combos = _market_basket_sku(cost_model, n)
    cat_combos = _market_basket_category(cost_model, n)
    return {
        "scenario":        f"market_basket_{n}",
        "n_suppliers":     n,
        "per_sku": {
            "combinations": sku_combos,
            "best":         sku_combos[0] if sku_combos else None,
        },
        "per_category": {
            "combinations": cat_combos,
            "best":         cat_combos[0] if cat_combos else None,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Award recommendation
# ═══════════════════════════════════════════════════════════════════════════════

def build_award_recommendation(
    total_costs: list[dict],
    bob: dict,
    opt_sku: dict,
    opt_cat: dict,
    basket_2: dict,
    basket_3: dict,
) -> dict:
    candidates = [
        {
            "strategy":          "L1 — Single Supplier (Lowest Total)",
            "total":             total_costs[0]["total_cost"] if total_costs else 0,
            "complexity":        "Low",
            "risk":              "Low",
            "suppliers_involved": 1,
        },
        {
            "strategy":    "Best of Best (per SKU, all suppliers)",
            "total":       bob.get("total_cost", 0),
            "complexity":  "Very High",
            "risk":        "High",
            "suppliers_involved": len([s for s, w in bob.get("wins_by_supplier", {}).items() if w > 0]),
        },
        {
            "strategy":    "Optimised Award — per SKU",
            "total":       opt_sku.get("total_cost", 0),
            "complexity":  "High",
            "risk":        "Medium",
            "suppliers_involved": opt_sku.get("supplier_count", 0),
        },
        {
            "strategy":    "Optimised Award — per Category",
            "total":       opt_cat.get("total_cost", 0),
            "complexity":  "Medium",
            "risk":        "Low",
            "suppliers_involved": opt_cat.get("supplier_count", 0),
        },
    ]
    if basket_2.get("per_category", {}).get("best"):
        b = basket_2["per_category"]["best"]
        candidates.append({
            "strategy":    "Market Basket — 2 Suppliers (per Category)",
            "total":       b["total_cost"],
            "complexity":  "Medium",
            "risk":        "Low",
            "suppliers_involved": 2,
            "suppliers":   b["suppliers"],
        })
    if basket_3.get("per_category", {}).get("best"):
        b = basket_3["per_category"]["best"]
        candidates.append({
            "strategy":    "Market Basket — 3 Suppliers (per Category)",
            "total":       b["total_cost"],
            "complexity":  "Medium",
            "risk":        "Medium",
            "suppliers_involved": 3,
            "suppliers":   b["suppliers"],
        })

    candidates = [c for c in candidates if c["total"] > 0]
    candidates.sort(key=lambda x: x["total"])

    max_total = max((c["total"] for c in candidates), default=0)
    for c in candidates:
        c["saving_vs_worst"]     = round(max_total - c["total"], 2)
        c["saving_vs_worst_pct"] = round((max_total - c["total"]) / max_total * 100, 1) if max_total else 0

    recommended = candidates[0] if candidates else {}
    if recommended.get("complexity") == "Very High":
        med = [c for c in candidates if c["complexity"] in ("Low", "Medium")]
        if med:
            diff_pct = (candidates[0]["total"] - med[0]["total"]) / (med[0]["total"] or 1)
            if abs(diff_pct) < 0.03:
                recommended = med[0]

    rationale = [
        f"Recommended: {recommended.get('strategy')} at {recommended.get('total', 0):,.2f}",
        f"{recommended.get('suppliers_involved', 0)} supplier(s) — "
        f"{recommended.get('complexity', '')} complexity, {recommended.get('risk', '')} risk",
        f"Saves {recommended.get('saving_vs_worst_pct', 0):.1f}% vs highest-cost strategy",
    ]

    return {
        "recommended_strategy": recommended.get("strategy", ""),
        "recommended_total":    recommended.get("total", 0),
        "rationale":            rationale,
        "all_strategies":       candidates,
        "savings_opportunity":  round(max_total - recommended.get("total", 0), 2),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Master runner
# ═══════════════════════════════════════════════════════════════════════════════

def run_pricing_analysis(suppliers_pricing: list[dict]) -> dict:
    if not suppliers_pricing:
        return {"error": "No supplier pricing data provided"}

    cost_model  = build_cost_model(suppliers_pricing)
    total_costs = scenario_total_cost(suppliers_pricing)
    bob         = scenario_best_of_best(cost_model)
    opt_sku     = scenario_optimised_award_sku(cost_model)
    opt_cat     = scenario_optimised_award_category(cost_model)
    basket_2    = scenario_market_basket(cost_model, 2)
    basket_3    = scenario_market_basket(cost_model, 3)
    award_rec   = build_award_recommendation(
        total_costs, bob, opt_sku, opt_cat, basket_2, basket_3
    )

    return {
        "suppliers":                [sp["supplier_name"] for sp in suppliers_pricing],
        "cost_model":               cost_model,
        "total_costs":              total_costs,
        "best_of_best":             bob,
        "optimised_award_sku":      opt_sku,
        "optimised_award_category": opt_cat,
        "market_basket_2":          basket_2,
        "market_basket_3":          basket_3,
        "award_recommendation":     award_rec,
    }


# ─── backward-compat alias ────────────────────────────────────────────────────
# pricing_agent.py imports `analyze_pricing`; this alias satisfies that import
# without changing any existing callers of run_pricing_analysis.
analyze_pricing = run_pricing_analysis
