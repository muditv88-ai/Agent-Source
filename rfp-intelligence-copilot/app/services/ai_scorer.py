"""Agentic AI scorer - scores each question with rationale for quant and qual."""
import os
import json
from openai import OpenAI
from typing import List, Dict, Any

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

SCORING_SYSTEM_PROMPT = """
You are an expert procurement evaluator. Score a supplier's answer to an RFP question.

Scoring rules:
- Score from 0 to 10 (decimals allowed)
- For QUANTITATIVE questions (price, date, number, percentage):
  * Compare the supplier's value against context of what is good/bad
  * Lower price = higher score, faster delivery = higher score, etc.
  * Be precise and reference the actual numbers
- For QUALITATIVE questions (approach, experience, methodology):
  * Score based on specificity, relevance, demonstrated capability
  * 0-3: Vague or no response
  * 4-6: Adequate but generic
  * 7-9: Strong with specific evidence
  * 10: Exceptional, best-in-class response

Return JSON:
{
  "score": 7.5,
  "rationale": "Detailed explanation referencing the actual answer..."
}

Only return valid JSON.
"""


def score_question(
    question: Dict[str, Any],
    supplier_answer: str,
    all_supplier_answers: Dict[str, str] = None,
) -> Dict[str, Any]:
    """Score a single question for one supplier with full rationale."""
    context = ""
    if all_supplier_answers and question["question_type"] == "quantitative":
        # Provide all suppliers' answers for relative quantitative scoring
        context = "\nOther suppliers' answers for context:\n"
        for supplier, answer in all_supplier_answers.items():
            context += f"- {supplier}: {answer}\n"

    prompt = (
        f"Question: {question['question_text']}\n"
        f"Type: {question['question_type']}\n"
        f"Weight: {question['weight']}%\n"
        f"Scoring Guidance: {question.get('scoring_guidance', 'None')}\n"
        f"Supplier Answer: {supplier_answer}"
        f"{context}"
    )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SCORING_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )

    return json.loads(response.choices[0].message.content)


def generate_supplier_summary(
    supplier_name: str,
    category_scores: List[Dict],
    overall_score: float,
) -> Dict[str, Any]:
    """Generate strengths, weaknesses and recommendation for a supplier."""
    scores_text = "\n".join(
        f"- {c['category']}: {c['weighted_score']:.1f}/10" for c in category_scores
    )

    prompt = (
        f"Supplier: {supplier_name}\n"
        f"Overall Score: {overall_score:.1f}/10\n"
        f"Category Scores:\n{scores_text}\n\n"
        "Based on these scores, provide:\n"
        "1. Top 3 strengths\n"
        "2. Top 3 weaknesses\n"
        "3. One sentence recommendation (award / consider / reject)"
    )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": "You are a procurement advisor. Return JSON with keys: strengths (list), weaknesses (list), recommendation (string).",
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    return json.loads(response.choices[0].message.content)
