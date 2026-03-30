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
You are an expert procurement analyst. Given an RFP's list of questions and a supplier's
response document, extract each supplier's answer to each question.

Return ONLY a valid JSON object, no explanation:
{
  "supplier_name": "name of supplier if identifiable, else 'Unknown Supplier'",
  "answers": {
    "Q1": "supplier's answer to question 1",
    "Q2": "supplier's answer to question 2"
  }
}

If a question is not answered, use "No response provided".
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


def extract_supplier_answers(
    supplier_document_text: str,
    questions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Map supplier document answers to RFP questions using Nemotron."""
    questions_summary = "\n".join(
        f"{q['question_id']}: {q['question_text']}" for q in questions
    )
    truncated_doc = supplier_document_text[:10000]

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "detailed thinking off"},
            {
                "role": "user",
                "content": (
                    f"{SYSTEM_PROMPT}\n\n"
                    f"RFP Questions:\n{questions_summary}\n\n"
                    f"Supplier Response Document:\n{truncated_doc}"
                ),
            },
        ],
        temperature=0.1,
        max_tokens=4096,
    )

    return _parse_json(_extract_content(response))
