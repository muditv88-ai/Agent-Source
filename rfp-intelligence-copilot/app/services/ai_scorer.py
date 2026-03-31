"""
ai_scorer.py — dual-LLM scoring with price analysis.

Primary model  : nvidia/llama-3.1-nemotron-ultra-253b-v1  (NVIDIA)
Checker model  : meta/llama-3.3-70b-instruct              (NVIDIA, faster)

Flow:
  1. Primary model scores each question.
  2. Checker model independently scores the same question.
  3. If |primary - checker| > DISAGREEMENT_THRESHOLD, scores are averaged
     and the question is flagged for human review.
  4. Price extraction uses a dedicated prompt to pull structured pricing
     from Commercial/Pricing sections.
"""
import os
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional
from openai import OpenAI

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)

PRIMARY_MODEL  = "nvidia/llama-3.1-nemotron-ultra-253b-v1"
CHECKER_MODEL  = "meta/llama-3.3-70b-instruct"
DISAGREEMENT_THRESHOLD = 2.0   # flag if |primary - checker| >= this

# ── Prompts ───────────────────────────────────────────────────────────────────

SCORING_SYSTEM = """
You are an expert procurement evaluator. Score a supplier's answer to an RFP question.

Scoring rules:
- Score 0–10 (decimals allowed)
- QUANTITATIVE: lower price/faster delivery = higher score (use context of other suppliers)
- QUALITATIVE:
  0-3: vague / no response
  4-6: adequate but generic
  7-9: strong with specific evidence
  10:  exceptional, best-in-class

Return ONLY valid JSON, nothing else:
{"score": 7.5, "rationale": "one concise sentence"}
"""

PRICE_EXTRACTION_SYSTEM = """
You are a pricing analyst. Extract ALL line-item prices from the text below.
Match items to the RFP template structure where possible.

Return ONLY valid JSON array:
[
  {"line_item": "item name", "value": "123.45", "unit": "USD"},
  ...
]
If no prices found return [].
"""


# ── Utilities ───────────────────────────────────────────────────────────────────

def _extract_content(response) -> str:
    msg = response.choices[0].message
    if msg.content:
        return msg.content.strip()
    if hasattr(msg, "reasoning_content") and msg.reasoning_content:
        return msg.reasoning_content.strip()
    return str(msg)


def _parse_json(raw: str) -> Dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{[^{}]*\})", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    if raw.startswith("{") and not raw.endswith("}"):
        score_m = re.search(r'"score"\s*:\s*([\d.]+)', raw)
        rat_m   = re.search(r'"rationale"\s*:\s*"([^"]*)', raw)
        if score_m:
            return {
                "score": float(score_m.group(1)),
                "rationale": rat_m.group(1) if rat_m else "See evaluation."
            }
    raise ValueError(f"Could not parse JSON: {raw[:300]}")


def _parse_json_array(raw: str) -> list:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except Exception:
        match = re.search(r"(\[[^\[\]]*\])", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                pass
    return []


def _call_model(model: str, system: str, user: str, max_tokens: int = 256) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "detailed thinking off"},
            {"role": "user",   "content": f"{system}\n\n{user}"},
        ],
        temperature=0.1,
        max_tokens=max_tokens,
    )
    return _extract_content(resp)


# ── Core scoring ───────────────────────────────────────────────────────────────────

def score_question(
    question: Dict[str, Any],
    supplier_answer: str,
    all_supplier_answers: Dict[str, str] = None,
    dual_llm: bool = True,
) -> Dict[str, Any]:
    """
    Score a single question.
    Returns dict with: score, primary_score, checker_score, score_delta,
                       flagged, rationale, checker_rationale.
    """
    context = ""
    if all_supplier_answers and question["question_type"] == "quantitative":
        context = "\nOther suppliers' answers for context:\n" + "".join(
            f"- {s}: {a}\n" for s, a in all_supplier_answers.items()
        )

    prompt = (
        f"Question: {question['question_text']}\n"
        f"Type: {question['question_type']}\n"
        f"Weight: {question['weight']}%\n"
        f"Scoring Guidance: {question.get('scoring_guidance', 'None')}\n"
        f"Supplier Answer: {supplier_answer[:600]}"
        f"{context}"
    )

    # Run primary always; run checker only when dual_llm=True
    if dual_llm:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_primary = ex.submit(_call_model, PRIMARY_MODEL, SCORING_SYSTEM, prompt, 256)
            f_checker = ex.submit(_call_model, CHECKER_MODEL,  SCORING_SYSTEM, prompt, 256)
            primary_raw = f_primary.result()
            checker_raw = f_checker.result()
        primary = _parse_json(primary_raw)
        try:
            checker = _parse_json(checker_raw)
        except Exception:
            checker = {"score": primary["score"], "rationale": "Checker parse failed"}
    else:
        primary_raw = _call_model(PRIMARY_MODEL, SCORING_SYSTEM, prompt, 256)
        primary = _parse_json(primary_raw)
        checker = {"score": primary["score"], "rationale": ""}

    p_score = float(primary["score"])
    c_score = float(checker["score"])
    delta   = abs(p_score - c_score)
    flagged = delta >= DISAGREEMENT_THRESHOLD

    # Reconcile: average when flagged, use primary otherwise
    final_score = round((p_score + c_score) / 2, 2) if flagged else p_score

    return {
        "score":             final_score,
        "primary_score":     p_score,
        "checker_score":     c_score,
        "score_delta":       round(delta, 2),
        "flagged":           flagged,
        "rationale":         primary.get("rationale", ""),
        "checker_rationale": checker.get("rationale", ""),
    }


def score_questions_parallel(
    questions: List[Dict],
    supplier_answer: str,
    answers: Dict[str, str],
    cross_answers: Dict[str, Dict],
    supplier_name: str,
    dual_llm: bool = True,
    max_workers: int = 8,
) -> Dict[str, Dict]:
    """
    Score all questions for one supplier in parallel threads.
    Returns {question_id: score_data}.
    """
    results: Dict[str, Dict] = {}

    def _score_one(q):
        qid    = q["question_id"]
        answer = answers.get(qid, "No response provided")
        others = {k: v for k, v in cross_answers.get(qid, {}).items() if k != supplier_name}
        try:
            return qid, score_question(q, answer, others, dual_llm=dual_llm)
        except Exception:
            return qid, {"score": 0, "primary_score": 0, "checker_score": 0,
                         "score_delta": 0, "flagged": False,
                         "rationale": "Scoring failed", "checker_rationale": ""}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_score_one, q): q["question_id"] for q in questions}
        for fut in as_completed(futures):
            qid, data = fut.result()
            results[qid] = data

    return results


# ── Price extraction ──────────────────────────────────────────────────────────────────

def extract_prices_from_text(
    text: str,
    rfp_template_text: str = "",
    supplier_name: str = "",
) -> List[Dict]:
    """
    Extract line-item prices from a supplier's commercial/pricing section.
    Uses rfp_template_text to align items to the RFP structure.
    Returns list of {line_item, value, unit}.
    """
    context = ""
    if rfp_template_text:
        context = f"\nRFP template pricing structure for reference:\n{rfp_template_text[:1000]}\n"

    prompt = (
        f"{context}"
        f"Supplier: {supplier_name}\n"
        f"Text to extract prices from:\n{text[:2000]}"
    )
    try:
        raw  = _call_model(CHECKER_MODEL, PRICE_EXTRACTION_SYSTEM, prompt, 512)
        return _parse_json_array(raw)
    except Exception:
        return []


# ── Supplier summary ──────────────────────────────────────────────────────────────────

def generate_supplier_summary(
    supplier_name: str,
    category_scores: List[Dict],
    overall_score: float,
    technical_score: float = 0.0,
    commercial_score: float = 0.0,
    flagged_count: int = 0,
) -> Dict[str, Any]:
    scores_text = "\n".join(
        f"- {c['category']}: {c['weighted_score']:.1f}/10" for c in category_scores
    )
    flag_note = f"\n{flagged_count} questions were flagged by dual-LLM review." if flagged_count else ""

    prompt = (
        f"Supplier: {supplier_name}\n"
        f"Overall Score: {overall_score:.1f}/10  "
        f"(Technical: {technical_score:.1f}/10, Commercial: {commercial_score:.1f}/10)\n"
        f"Category Scores:\n{scores_text}{flag_note}\n\n"
        "Return ONLY JSON: {\"strengths\": [3 short strings], "
        "\"weaknesses\": [3 short strings], \"recommendation\": \"one sentence\"}"
    )
    try:
        raw = _call_model(PRIMARY_MODEL, "", prompt, 512)
        return _parse_json(raw)
    except Exception:
        return {"strengths": [], "weaknesses": [], "recommendation": ""}
