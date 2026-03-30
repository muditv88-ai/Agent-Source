"""Aggregates question-level scores to category and supplier level."""
from typing import List, Dict, Any


def aggregate_scores(
    questions: List[Dict[str, Any]],
    question_scores: Dict[str, Dict[str, Any]],  # {question_id: {score, rationale}}
    supplier_answers: Dict[str, str],             # {question_id: answer}
    supplier_name: str,
) -> List[Dict[str, Any]]:
    """Group question scores by category and compute weighted category scores."""
    categories: Dict[str, List] = {}

    for q in questions:
        cat = q["category"]
        if cat not in categories:
            categories[cat] = []

        qid = q["question_id"]
        score_data = question_scores.get(qid, {"score": 0, "rationale": "Not scored"})

        categories[cat].append({
            "question_id": qid,
            "question_text": q["question_text"],
            "category": cat,
            "question_type": q["question_type"],
            "weight": q["weight"],
            "score": score_data["score"],
            "rationale": score_data["rationale"],
            "supplier_answer": supplier_answers.get(qid, "No response provided"),
        })

    category_results = []
    for cat, qs in categories.items():
        total_weight = sum(q["weight"] for q in qs)
        if total_weight == 0:
            weighted_score = 0.0
        else:
            weighted_score = sum(q["score"] * q["weight"] for q in qs) / total_weight

        category_results.append({
            "category": cat,
            "weighted_score": round(weighted_score, 2),
            "question_count": len(qs),
            "questions": qs,
        })

    return category_results


def compute_overall_score(category_results: List[Dict[str, Any]], questions: List[Dict]) -> float:
    """Compute overall supplier score as weighted average across all questions."""
    total_weight = sum(q["weight"] for q in questions)
    if total_weight == 0:
        return 0.0
    total_score = sum(
        q_score["score"] * q_score["weight"]
        for cat in category_results
        for q_score in cat["questions"]
    )
    return round(total_score / total_weight, 2)
