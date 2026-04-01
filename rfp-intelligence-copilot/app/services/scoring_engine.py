"""
scoring_engine.py

Public interface for all scoring operations.
Delegates to ai_scorer.py (full LLM-based scoring) with a fast
weighted-average fallback for when the LLM is unavailable.

All aggregator.py and route imports go through this module.
"""
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class SafeDict(dict):
    def __missing__(self, key):
        return 0.0


# ---------------------------------------------------------------------------
# Core weighted scoring (no LLM required)
# ---------------------------------------------------------------------------

def compute_weighted_score(items: List[dict]) -> float:
    """
    Compute a weighted average score from a list of scored items.

    Each item must have:
        score  (float, 0–100)
        weight (float, positive)

    Missing keys are treated as 0 / 1 respectively.
    """
    total = 0.0
    weight_sum = 0.0
    for item in items:
        score = float(item.get("score", 0))
        weight = float(item.get("weight", 1))
        total += score * weight
        weight_sum += weight
    return round(total / weight_sum, 4) if weight_sum else 0.0


def rank_suppliers(supplier_scores: Dict[str, float]) -> List[dict]:
    """
    Return a ranked list of suppliers from highest to lowest score.

    Args:
        supplier_scores: {supplier_id: score, ...}

    Returns:
        [{"rank": 1, "supplier_id": ..., "score": ...}, ...]
    """
    ranked = sorted(supplier_scores.items(), key=lambda x: x[1], reverse=True)
    return [
        {"rank": i + 1, "supplier_id": sid, "score": round(score, 4)}
        for i, (sid, score) in enumerate(ranked)
    ]


def normalise_scores(raw_scores: Dict[str, float]) -> Dict[str, float]:
    """
    Normalise a dict of raw scores to a 0–100 scale.
    Handles edge case where all scores are identical (returns 50 for all).
    """
    if not raw_scores:
        return {}
    min_s = min(raw_scores.values())
    max_s = max(raw_scores.values())
    if max_s == min_s:
        return {k: 50.0 for k in raw_scores}
    return {
        k: round((v - min_s) / (max_s - min_s) * 100, 4)
        for k, v in raw_scores.items()
    }


# ---------------------------------------------------------------------------
# LLM-based scoring (delegates to ai_scorer.py)
# ---------------------------------------------------------------------------

class ScoringEngine:
    """
    Unified interface for all scoring operations.
    Routes to ai_scorer for LLM-assisted scoring, falls back to
    compute_weighted_score() for rule-based scoring.

    Usage:
        engine = ScoringEngine()
        result = engine.score_supplier_response(questions, response_text)
    """

    def __init__(self):
        try:
            from app.services.ai_scorer import AIScorer
            self._ai = AIScorer()
            self._ai_available = True
        except Exception as e:
            logger.warning("AIScorer unavailable (%s) — falling back to rule-based scoring", e)
            self._ai = None
            self._ai_available = False

    def score_supplier_response(
        self,
        questions: List[dict],
        response_text: str,
        supplier_id: Optional[str] = None,
    ) -> dict:
        """
        Score a supplier's response against evaluation questions.
        Returns {"items": [{question, score, weight, justification}], "overall": float}.
        """
        if self._ai_available and self._ai:
            try:
                return self._ai.score(questions, response_text, supplier_id=supplier_id)
            except Exception as e:
                logger.warning("AIScorer.score() failed (%s) — using fallback", e)

        # Rule-based fallback: score each question at 50 (neutral)
        items = [
            {
                "question": q.get("text", str(q)),
                "score": 50.0,
                "weight": float(q.get("weight", 1)),
                "justification": "Scored at neutral (AI scorer unavailable)",
            }
            for q in questions
        ]
        return {
            "items": items,
            "overall": compute_weighted_score(items),
            "method": "fallback",
        }

    def rank(self, supplier_scores: Dict[str, float]) -> List[dict]:
        return rank_suppliers(supplier_scores)

    def normalise(self, raw_scores: Dict[str, float]) -> Dict[str, float]:
        return normalise_scores(raw_scores)
