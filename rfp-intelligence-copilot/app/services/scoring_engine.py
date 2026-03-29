def compute_weighted_score(items):
    """Compute weighted score from scored items"""
    total = 0.0
    weight_sum = 0.0
    for item in items:
        score = float(item.get("score", 0))
        weight = float(item.get("weight", 1))
        total += score * weight
        weight_sum += weight
    return round(total / weight_sum, 4) if weight_sum else 0.0