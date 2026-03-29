from copy import deepcopy
from app.services.scoring_engine import compute_weighted_score

def run_scenario(base_suppliers, weight_adjustments=None, excluded_suppliers=None, compliance_threshold=None):
    weight_adjustments = weight_adjustments or {}
    excluded_suppliers = set(excluded_suppliers or [])
    results = []
    for s in base_suppliers:
        if s["supplier_id"] in excluded_suppliers:
            continue
        items = deepcopy(s["items"])
        for item in items:
            qid = item.get("question_id")
            if qid in weight_adjustments:
                item["weight"] = weight_adjustments[qid]
        total = compute_weighted_score(items)
        if compliance_threshold is not None and s.get("compliance_score", 1) < compliance_threshold:
            total = 0
        results.append({**s, "total_score": total})
    return sorted(results, key=lambda x: x["total_score"], reverse=True)