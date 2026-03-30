"""
pricing_analyzer.py

Builds cost models and runs award scenarios across suppliers:
1. Total Cost per Supplier
2. Best of Best (lowest per line item)
3. Overall Best Supplier (lowest total)
4. Market Basket 2 (optimal split across 2 suppliers by category)
5. Market Basket 3 (optimal split across 3 suppliers by category)
6. AI Award Strategy Recommendation
"""
import json
from itertools import combinations
from typing import Any


# ── Cost model builder ────────────────────────────────────────────────────────

def build_cost_model(suppliers_pricing: list[dict]) -> dict:
    """
    suppliers_pricing: list of extract_pricing_from_document() outputs.
    Returns a unified cost model keyed by description.
    """
    # Collect all unique line item descriptions across suppliers
    all_descriptions = []
    seen = set()
    for sp in suppliers_pricing:
        for item in sp.get("all_line_items", []):
            d = item["description"].strip()
            if d and d not in seen:
                all_descriptions.append(d)
                seen.add(d)
    
    # Build matrix: description -> {supplier_name: {unit_price, total, qty, category}}
    matrix: dict[str, dict] = {}
    for desc in all_descriptions:
        matrix[desc] = {}
        for sp in suppliers_pricing:
            sname = sp["supplier_name"]
            match = next(
                (i for i in sp.get("all_line_items", []) if i["description"].strip() == desc),
                None
            )
            if match:
                matrix[desc][sname] = {
                    "unit_price": match["unit_price"],
                    "quantity":   match["quantity"],
                    "total":      match["total"],
                    "category":   match["category"],
                    "notes":      match["notes"],
                }
            else:
                matrix[desc][sname] = None  # supplier did not price this item
    
    return {
        "descriptions": all_descriptions,
        "suppliers":    [sp["supplier_name"] for sp in suppliers_pricing],
        "matrix":       matrix,
    }


# ── Scenario 1: Total cost per supplier ──────────────────────────────────────

def scenario_total_cost(suppliers_pricing: list[dict]) -> list[dict]:
    results = []
    for sp in suppliers_pricing:
        total = sp.get("total_cost", 0.0)
        by_category: dict[str, float] = {}
        for item in sp.get("all_line_items", []):
            cat = item["category"] or "Uncategorised"
            by_category[cat] = round(by_category.get(cat, 0.0) + item["total"], 2)
        results.append({
            "supplier_name": sp["supplier_name"],
            "total_cost":    round(total, 2),
            "by_category":   by_category,
            "line_item_count": len(sp.get("all_line_items", [])),
        })
    results.sort(key=lambda x: x["total_cost"])
    for i, r in enumerate(results):
        r["rank"] = i + 1
    return results


# ── Scenario 2: Best of best ──────────────────────────────────────────────────

def scenario_best_of_best(cost_model: dict) -> dict:
    """
    For each line item, pick the supplier with the lowest total.
    Returns total cost and a breakdown of which supplier wins each item.
    """
    matrix      = cost_model["matrix"]
    suppliers   = cost_model["suppliers"]
    breakdown   = []
    total       = 0.0
    savings_vs  = {s: 0.0 for s in suppliers}  # savings vs awarding 100% to each supplier
    
    for desc, supplier_map in matrix.items():
        priced = {s: v for s, v in supplier_map.items() if v is not None and v["total"] > 0}
        if not priced:
            continue
        best_supplier = min(priced, key=lambda s: priced[s]["total"])
        best_val      = priced[best_supplier]
        total        += best_val["total"]
        
        breakdown.append({
            "description":    desc,
            "best_supplier":  best_supplier,
            "best_total":     best_val["total"],
            "best_unit_price": best_val["unit_price"],
            "quantity":       best_val["quantity"],
            "category":       best_val["category"],
            "all_prices":     {s: v["total"] if v else None for s, v in supplier_map.items()},
        })
    
    wins_by_supplier: dict[str, int] = {s: 0 for s in suppliers}
    for b in breakdown:
        wins_by_supplier[b["best_supplier"]] = wins_by_supplier.get(b["best_supplier"], 0) + 1
    
    return {
        "scenario": "best_of_best",
        "total_cost": round(total, 2),
        "breakdown": breakdown,
        "wins_by_supplier": wins_by_supplier,
    }


# ── Scenario 3: Overall best supplier ────────────────────────────────────────

def scenario_overall_best(total_cost_results: list[dict]) -> dict:
    best = total_cost_results[0] if total_cost_results else None
    if not best:
        return {"scenario": "overall_best", "supplier_name": None, "total_cost": 0}
    return {
        "scenario":      "overall_best",
        "supplier_name": best["supplier_name"],
        "total_cost":    best["total_cost"],
        "by_category":   best["by_category"],
        "vs_others":     [
            {
                "supplier_name": r["supplier_name"],
                "their_total":   r["total_cost"],
                "saving":        round(r["total_cost"] - best["total_cost"], 2),
                "saving_pct":    round((r["total_cost"] - best["total_cost"]) / r["total_cost"] * 100, 1)
                                 if r["total_cost"] > 0 else 0,
            }
            for r in total_cost_results[1:]
        ],
    }


# ── Market basket helper ──────────────────────────────────────────────────────

def _market_basket(cost_model: dict, n_suppliers: int) -> list[dict]:
    """
    For each combination of n_suppliers, find the optimal category split
    that minimises total cost (each category awarded to cheapest of the combo).
    """
    matrix    = cost_model["matrix"]
    suppliers = cost_model["suppliers"]
    
    if len(suppliers) < n_suppliers:
        return []
    
    # Build category totals per supplier
    cat_totals: dict[str, dict[str, float]] = {}
    for desc, supplier_map in matrix.items():
        for sname, val in supplier_map.items():
            if val is None:
                continue
            cat = val["category"] or "Uncategorised"
            if cat not in cat_totals:
                cat_totals[cat] = {}
            cat_totals[cat][sname] = cat_totals[cat].get(sname, 0.0) + val["total"]
    
    results = []
    for combo in combinations(suppliers, n_suppliers):
        total = 0.0
        allocation: dict[str, str] = {}
        cat_costs: dict[str, dict] = {}
        
        for cat, totals in cat_totals.items():
            combo_totals = {s: totals.get(s, float("inf")) for s in combo}
            best_s = min(combo_totals, key=lambda s: combo_totals[s])
            best_cost = combo_totals[best_s]
            if best_cost == float("inf"):
                continue
            total           += best_cost
            allocation[cat]  = best_s
            cat_costs[cat]   = {"awarded_to": best_s, "cost": round(best_cost, 2),
                                "all_costs": {s: round(combo_totals[s], 2) for s in combo}}
        
        results.append({
            "suppliers":  list(combo),
            "total_cost": round(total, 2),
            "allocation": allocation,
            "category_detail": cat_costs,
        })
    
    results.sort(key=lambda x: x["total_cost"])
    return results


# ── Scenario 4 & 5: Market basket ────────────────────────────────────────────

def scenario_market_basket_2(cost_model: dict) -> dict:
    combos = _market_basket(cost_model, 2)
    return {
        "scenario":     "market_basket_2",
        "combinations": combos,
        "best":         combos[0] if combos else None,
    }


def scenario_market_basket_3(cost_model: dict) -> dict:
    combos = _market_basket(cost_model, 3)
    return {
        "scenario":     "market_basket_3",
        "combinations": combos,
        "best":         combos[0] if combos else None,
    }


# ── Scenario 6: Award strategy recommendation ─────────────────────────────────

def build_award_recommendation(
    total_costs: list[dict],
    best_of_best: dict,
    overall_best: dict,
    basket_2: dict,
    basket_3: dict,
) -> dict:
    """
    Compares all scenarios and recommends the optimal award strategy.
    """
    candidates = [
        {"strategy": "Overall Best Supplier",  "total": overall_best.get("total_cost", 0),
         "complexity": "Low",  "risk": "Low",  "suppliers_involved": 1},
        {"strategy": "Best of Best",            "total": best_of_best.get("total_cost", 0),
         "complexity": "High", "risk": "High", "suppliers_involved": len(best_of_best.get("wins_by_supplier", {}))},
    ]
    if basket_2.get("best"):
        candidates.append({
            "strategy": "Market Basket (2 Suppliers)",
            "total": basket_2["best"]["total_cost"],
            "complexity": "Medium", "risk": "Medium",
            "suppliers_involved": 2,
            "suppliers": basket_2["best"]["suppliers"],
            "allocation": basket_2["best"]["allocation"],
        })
    if basket_3.get("best"):
        candidates.append({
            "strategy": "Market Basket (3 Suppliers)",
            "total": basket_3["best"]["total_cost"],
            "complexity": "High", "risk": "Medium",
            "suppliers_involved": 3,
            "suppliers": basket_3["best"]["suppliers"],
            "allocation": basket_3["best"]["allocation"],
        })
    
    # Sort by total cost
    candidates.sort(key=lambda x: x["total"])
    lowest = candidates[0]
    
    # Compute savings vs most expensive strategy
    max_total = max(c["total"] for c in candidates) if candidates else 0
    for c in candidates:
        c["saving_vs_worst"]     = round(max_total - c["total"], 2)
        c["saving_vs_worst_pct"] = round((max_total - c["total"]) / max_total * 100, 1) if max_total > 0 else 0
    
    # Recommended = lowest cost unless complexity is High — then prefer Medium if saving < 5%
    recommended = lowest
    if lowest["complexity"] == "High":
        medium_candidates = [c for c in candidates if c["complexity"] in ("Low", "Medium")]
        if medium_candidates:
            medium_best = medium_candidates[0]
            saving_diff = lowest["total"] - medium_best["total"]
            # If best-of-best only saves < 3% vs medium, prefer medium for simplicity
            if abs(saving_diff) / (medium_best["total"] or 1) < 0.03:
                recommended = medium_best
    
    rationale = [
        f"Lowest total cost: {recommended['strategy']} at {recommended['total']:,.2f}",
        f"Involves {recommended['suppliers_involved']} supplier(s) — {recommended['complexity'].lower()} management complexity",
        f"Saves {recommended['saving_vs_worst_pct']}% vs the highest-cost strategy",
    ]
    if recommended.get("allocation"):
        for cat, supplier in recommended["allocation"].items():
            rationale.append(f"  • {cat}: award to {supplier}")
    
    return {
        "recommended_strategy": recommended["strategy"],
        "recommended_total":    recommended["total"],
        "rationale":            rationale,
        "all_strategies":       candidates,
        "savings_opportunity":  round(max_total - recommended["total"], 2),
    }


# ── Master runner ─────────────────────────────────────────────────────────────

def run_pricing_analysis(suppliers_pricing: list[dict]) -> dict:
    """
    Run all 5 scenarios + award recommendation.
    suppliers_pricing: list of extract_pricing_from_document() outputs.
    """
    if not suppliers_pricing:
        return {"error": "No supplier pricing data provided"}
    
    cost_model   = build_cost_model(suppliers_pricing)
    total_costs  = scenario_total_cost(suppliers_pricing)
    bob          = scenario_best_of_best(cost_model)
    overall_best = scenario_overall_best(total_costs)
    basket_2     = scenario_market_basket_2(cost_model)
    basket_3     = scenario_market_basket_3(cost_model)
    award_rec    = build_award_recommendation(total_costs, bob, overall_best, basket_2, basket_3)
    
    return {
        "suppliers":         [sp["supplier_name"] for sp in suppliers_pricing],
        "cost_model":        cost_model,
        "total_costs":       total_costs,
        "best_of_best":      bob,
        "overall_best":      overall_best,
        "market_basket_2":   basket_2,
        "market_basket_3":   basket_3,
        "award_recommendation": award_rec,
    }
