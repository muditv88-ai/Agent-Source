"""Parses supplier response documents and maps answers to RFP questions."""
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

SYSTEM_PROMPT = """
You are an expert procurement analyst. Given an RFP's list of questions and a section of a
supplier's response document, extract the supplier's answer to each question.

Return ONLY a valid JSON object, no explanation:
{
  "supplier_name": "name of supplier if identifiable, else 'Unknown Supplier'",
  "answers": {
    "Q1": "supplier's answer to question 1",
    "Q2": "supplier's answer to question 2"
  }
}

Only include questions that have answers in THIS section. If a question is not answered here,
omit it — do not include it with 'No response provided'.
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


def _split_into_chunks(text: str, max_chars: int = 10000) -> List[str]:
    chunks = []
    while len(text) > max_chars:
        split_at = text.rfind("\n", 0, max_chars)
        if split_at == -1:
            split_at = max_chars
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    if text:
        chunks.append(text)
    return chunks


def extract_supplier_answers(
    supplier_document_text: str,
    questions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Map supplier document answers to ALL RFP questions by processing every sheet/chunk."""

    questions_summary = "\n".join(
        f"{q['question_id']}: {q['question_text']}" for q in questions
    )

    # Split on sheet boundaries first, then further chunk large sections
    sections = re.split(r"(?=^=== Sheet:)", supplier_document_text, flags=re.MULTILINE)
    sections = [s.strip() for s in sections if s.strip()]
    if not sections:
        sections = [supplier_document_text]

    merged_answers: Dict[str, str] = {}
    supplier_name = "Unknown Supplier"

    for section in sections:
        chunks = _split_into_chunks(section, max_chars=10000)
        for chunk in chunks:
            if not chunk.strip():
                continue
            try:
                response = client.chat.completions.create(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": "detailed thinking off"},
                        {
                            "role": "user",
                            "content": (
                                f"{SYSTEM_PROMPT}\n\n"
                                f"RFP Questions:\n{questions_summary}\n\n"
                                f"Supplier Response Section:\n{chunk}"
                            ),
                        },
                    ],
                    temperature=0.1,
                    max_tokens=4096,
                )
                result = _parse_json(_extract_content(response))
                # Keep first identified supplier name
                if supplier_name == "Unknown Supplier" and result.get("supplier_name"):
                    supplier_name = result["supplier_name"]
                # Merge answers — later chunks can fill in missing answers
                for qid, answer in result.get("answers", {}).items():
                    if qid not in merged_answers or merged_answers[qid] == "No response provided":
                        merged_answers[qid] = answer
            except Exception as e:
                print(f"Warning: supplier chunk extraction failed: {e}")
                continue

    # Fill in any questions that were never answered across all chunks
    for q in questions:
        if q["question_id"] not in merged_answers:
            merged_answers[q["question_id"]] = "No response provided"

    return {
        "supplier_name": supplier_name,
        "answers": merged_answers,
    }
