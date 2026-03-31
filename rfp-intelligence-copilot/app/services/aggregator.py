"""
aggregator.py — score aggregation with tech/commercial split.

Categories whose name contains any of COMMERCIAL_KEYWORDS are treated
as Commercial; everything else is Technical.

Final overall score = (tech_score * tech_weight) + (commercial_score * commercial_weight)
                      where weights are fractions summing to 1.0.
"""
from typing import List, Dict, Any

COMMERCIAL_KEYWORDS = {
    "commercial", "pricing", "price", "cost", "financial",
    "payment", "terms", "contract", "fee", "rate",
}


def _is_commercial(category: str) -> bool:
    cat_lower = category.lower()
    return any(kw in cat_lower for kw in COMMERCIAL_KEYWORDS)


def aggregate_scores(
    questions: List[Dict[str, Any]],
    question_scores: Dict[str, Dict[str, Any]],
    supplier_answers: Dict[str, str],
    supplier_name: str,
) -> List[Dict[str, Any]]:
    """Group question scores by category, compute weighted category scores."""
    categories: Dict[str, List] = {}

    for q in questions:
        cat = q["category"]
        if cat not in categories:
            categories[cat] = []

        qid        = q["question_id"]
        score_data = question_scores.get(qid, {
            "score": 0, "primary_score": 0, "checker_score": 0,
            "score_delta": 0, "flagged": False,
            "rationale": "Not scored", "checker_rationale": ""
        })

        categories[cat].append({
            "question_id":       qid,
            "question_text":     q["question_text"],
            "category":          cat,
            "question_type":     q["question_type"],
            "weight":            q["weight"],
            "score":             score_data["score"],
            "primary_score":     score_data.get("primary_score", score_data["score"]),
            "checker_score":     score_data.get("checker_score", score_data["score"]),
            "score_delta":       score_data.get("score_delta", 0),
            "flagged":           score_data.get("flagged", False),
            "rationale":         score_data.get("rationale", ""),
            "checker_rationale": score_data.get("checker_rationale", ""),
            "supplier_answer":   supplier_answers.get(qid, "No response provided"),
        })

    category_results = []
    for cat, qs in categories.items():
        total_weight = sum(q["weight"] for q in qs)
        weighted_score = (
            sum(q["score"] * q["weight"] for q in qs) / total_weight
            if total_weight else 0.0
        )
        category_results.append({
            "category":       cat,
            "weighted_score": round(weighted_score, 2),
            "question_count": len(qs),
            "questions":      qs,
            "is_commercial":  _is_commercial(cat),
        })

    return category_results


def compute_split_scores(
    category_results: List[Dict[str, Any]],
    tech_weight: float = 70.0,
    commercial_weight: float = 30.0,
) -> Dict[str, float]:
    """
    Compute technical score, commercial score, and weighted overall score.

    Returns:
        {"technical_score", "commercial_score", "overall_score"}
    """
    tech_qs       = [q for cat in category_results if not cat["is_commercial"] for q in cat["questions"]]
    commercial_qs = [q for cat in category_results if cat["is_commercial"]     for q in cat["questions"]]

    def _weighted_avg(qs):
        tw = sum(q["weight"] for q in qs)
        return round(sum(q["score"] * q["weight"] for q in qs) / tw, 2) if tw else 0.0

    t_score = _weighted_avg(tech_qs)
    c_score = _weighted_avg(commercial_qs) if commercial_qs else t_score  # fallback if no commercial section

    tw = tech_weight / 100
    cw = commercial_weight / 100
    # Normalise so they always sum to 1
    total = tw + cw
    if total:
        tw /= total
        cw /= total

    overall = round(t_score * tw + c_score * cw, 2)

    return {
        "technical_score":   t_score,
        "commercial_score":  c_score,
        "overall_score":     overall,
    }


# Keep old function for backwards compat
def compute_overall_score(category_results, questions) -> float:
    from app.services.aggregator import compute_split_scores
    return compute_split_scores(category_results)["overall_score"]
