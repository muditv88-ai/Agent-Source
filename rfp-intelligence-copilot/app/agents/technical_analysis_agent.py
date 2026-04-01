"""
technical_analysis_agent.py

Handles FM-6.1 through FM-6.5:
  - Parallel AI scoring via ai_scorer.py (unchanged)
  - Dynamic weight overrides per session
  - Gap analysis (weak / below-threshold questions)
  - Disqualification rules
  - Per-supplier narrative report

ai_scorer.py is NOT modified — this agent wraps it cleanly.
"""
import logging
from typing import Any, Dict, List, Optional

from app.agents.base_agent import BaseAgent, Tool
from app.services.ai_scorer import score_questions_parallel, generate_supplier_summary

logger = logging.getLogger(__name__)


class TechnicalAnalysisAgent(BaseAgent):
    """
    Wraps ai_scorer.py and adds:
      - Per-category weight overrides (from UI sliders)
      - Gap analysis returning weak areas per supplier
      - Disqualification flag when too many low scores
      - Narrative report generation
    """

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        min_score: float = 4.0,
        disqualify_threshold: float = 2.0,
        disqualify_max_weak: int = 2,
    ):
        self.weights = weights or {}
        self.min_score = min_score
        self.disqualify_threshold = disqualify_threshold
        self.disqualify_max_weak = disqualify_max_weak

        tools = [
            Tool(
                name="score_all_suppliers",
                description="Run parallel AI scoring for all supplier responses",
                fn=self._score_all,
                schema={"type": "object"},
            ),
            Tool(
                name="run_gap_analysis",
                description="Identify weak and unanswered requirements per supplier",
                fn=self._gap_analysis,
                schema={"type": "object"},
            ),
            Tool(
                name="generate_report",
                description="Generate a narrative evaluation report for a supplier",
                fn=self._generate_report,
                schema={"type": "object"},
            ),
        ]
        super().__init__(tools)

    def _apply_weight_overrides(self, questions: list) -> list:
        """Apply any user-supplied category weight overrides."""
        if not self.weights:
            return questions
        updated = []
        for q in questions:
            q = dict(q)
            if q.get("category") in self.weights:
                q["weight"] = self.weights[q["category"]]
            updated.append(q)
        return updated

    def _score_all(
        self, questions: list, supplier_responses: Dict[str, dict]
    ) -> Dict[str, dict]:
        """
        supplier_responses: {supplier_name: {question_id: answer_text}}
        Returns:            {supplier_name: {question_id: {score, rationale}}}
        """
        questions = self._apply_weight_overrides(questions)

        # Build cross-supplier answers map for comparative scoring
        cross_answers = {
            qid: {s: r.get(qid, "") for s, r in supplier_responses.items()}
            for qid in {q["question_id"] for q in questions}
        }

        results = {}
        for supplier_name, answers in supplier_responses.items():
            try:
                scores = score_questions_parallel(
                    questions=questions,
                    answers=answers,
                    cross_answers=cross_answers,
                    supplier_name=supplier_name,
                )
                results[supplier_name] = scores
            except Exception as e:
                logger.error("Scoring failed for %s: %s", supplier_name, e)
                results[supplier_name] = {}
        return results

    def _gap_analysis(
        self, supplier_scores: Dict[str, dict], questions: list
    ) -> Dict[str, dict]:
        gaps = {}
        for supplier, scores in supplier_scores.items():
            weak = [
                {
                    "question_id": qid,
                    "score": data["score"],
                    "rationale": data.get("rationale", ""),
                }
                for qid, data in scores.items()
                if isinstance(data, dict) and data.get("score", 10) < self.min_score
            ]
            critically_weak = [
                w for w in weak if w["score"] < self.disqualify_threshold
            ]
            gaps[supplier] = {
                "weak_areas": weak,
                "disqualified": len(critically_weak) > self.disqualify_max_weak,
                "weak_count": len(weak),
            }
        return gaps

    def _generate_report(
        self,
        supplier_name: str,
        category_scores: list,
        overall_score: float,
    ) -> dict:
        return generate_supplier_summary(
            supplier_name=supplier_name,
            category_scores=category_scores,
            overall_score=overall_score,
        )

    def run(self, input: dict, context: Optional[Dict] = None) -> dict:
        questions = input["questions"]
        supplier_responses = input["supplier_responses"]

        scores = self._score_all(questions, supplier_responses)
        gaps = self._gap_analysis(scores, questions)

        reports = {}
        for supplier, supplier_scores in scores.items():
            if not supplier_scores:
                continue
            score_values = [
                s["score"] for s in supplier_scores.values() if isinstance(s, dict)
            ]
            overall = round(sum(score_values) / max(len(score_values), 1), 2)
            reports[supplier] = self._generate_report(
                supplier_name=supplier,
                category_scores=[],
                overall_score=overall,
            )

        return {
            "scores": scores,
            "gaps": gaps,
            "reports": reports,
            "disqualified": [
                s for s, g in gaps.items() if g.get("disqualified")
            ],
        }
