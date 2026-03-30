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
- For QUANTITATIVE questions: lower price = higher score, faster delivery = higher score
- For QUALITATIVE questions:
  * 0-3: Vague or no response
  * 4-6: Adequate but generic
  * 7-9: Strong with specific evidence
  * 10: Exceptional, best-in-class

Return ONLY this JSON, nothing else:
{"score": 7.5, "rationale": "one sentence explanation"}
"""


def _extract_content(response) -> str:
    msg = response.choices[0].message
    if msg.content:
        return msg.content.strip()
    if hasattr(msg, "reasoning_content") and msg.reasoning_content:
        return msg.reasoning_content.strip()
    return str(msg)


def _parse_json(raw: str) -> Dict:
    """Parse JSON robustly, recovering from truncation and markdown fences."""
    raw = raw.strip()
    # Strip markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    # Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Find first complete {...} block
    match = re.search(r"(\{[^{}]*\})", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to recover truncated JSON by closing open braces
    if raw.startswith("{") and not raw.endswith("}"):
        # Extract what we can with regex for score and rationale
        score_match = re.search(r'"score"\s*:\s*([\d.]+)', raw)
        rationale_match = re.search(r'"rationale"\s*:\s*"([^"]*)', raw)
        if score_match:
            return {
                "score": float(score_match.group(1)),
                "rationale": rationale_match.group(1) if rationale_match else "See evaluation."
            }

    raise ValueError(f"Could not parse JSON from response: {raw[:300]}")


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
        f"Supplier Answer: {supplier_answer[:500]}"
        f"{context}"
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "detailed thinking off"},
            {"role": "user", "content": f"{SCORING_SYSTEM_PROMPT}\n\n{prompt}"},
        ],
        temperature=0.1,
        max_tokens=256,
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
        "Return ONLY JSON with keys: strengths (list of 3 short strings), "
        "weaknesses (list of 3 short strings), recommendation (one sentence string)."
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "detailed thinking off"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=512,
    )
    return _parse_json(_extract_content(response))
