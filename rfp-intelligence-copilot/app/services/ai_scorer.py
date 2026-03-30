"""Agentic AI scorer using NVIDIA Nemotron - scores each question with rationale."""
import os
import json
import re
from openai import OpenAI
from typing import List, Dict, Any

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)

MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

SCORING_SYSTEM_PROMPT = """
You are an expert procurement evaluator. Score a supplier's answer to an RFP question.

Scoring rules:
- Score from 0 to 10 (decimals allowed)
- For QUANTITATIVE questions (price, date, number, percentage):
  * Lower price = higher score, faster delivery = higher score
  * Be precise and reference the actual numbers
- For QUALITATIVE questions (approach, experience, methodology):
  * 0-3: Vague or no response
  * 4-6: Adequate but generic
  * 7-9: Strong with specific evidence
  * 10: Exceptional, best-in-class response

Return ONLY valid JSON, no explanation:
{
  "score": 7.5,
  "rationale": "Explanation referencing the actual answer..."
}
"""


def _extract_content(response) -> str:
    msg = response.choices[0].message
    if msg.content:
        return msg.content.strip()
    if hasattr(msg, "reasoning_content") and msg.reasoning_content:
        return msg.reasoning_content.strip()
    return str(msg)


def _parse_json(raw: str) -> Dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw.strip())
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*\})", raw, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    raise ValueError(f"Could not parse JSON from response: {raw[:500]}")


def score_question(
    question: Dict[str, Any],
    supplier_answer: str,
    all_supplier_answers: Dict[str, str] = None,
) -> Dict[str, Any]:
    context = ""
    if all_supplier_answers and question["question_type"] == "quantitative":
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
        model=MODEL,
        messages=[
            {"role": "system", "content": "detailed thinking off"},
            {"role": "user", "content": f"{SCORING_SYSTEM_PROMPT}\n\n{prompt}"},
        ],
        temperature=0.1,
        max_tokens=1024,
    )
    return _parse_json(_extract_content(response))


def generate_supplier_summary(
    supplier_name: str,
    category_scores: List[Dict],
    overall_score: float,
) -> Dict[str, Any]:
    scores_text = "\n".join(
        f"- {c['category']}: {c['weighted_score']:.1f}/10" for c in category_scores
    )

    prompt = (
        f"Supplier: {supplier_name}\n"
        f"Overall Score: {overall_score:.1f}/10\n"
        f"Category Scores:\n{scores_text}\n\n"
        "Provide top 3 strengths, top 3 weaknesses, "
        "and one sentence recommendation (award / consider / reject). "
        "Return ONLY JSON with keys: strengths (list), weaknesses (list), recommendation (string)."
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "detailed thinking off"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=1024,
    )
    return _parse_json(_extract_content(response))
