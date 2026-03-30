"""Uses NVIDIA Nemotron to extract structured questions from any RFP document."""
import os
import json
import re
from openai import OpenAI
from typing import Dict, Any

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)

MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

SYSTEM_PROMPT = """
You are an expert procurement analyst. Extract all evaluation questions from an RFP document.

For each question or evaluation criterion, extract:
- question_id: sequential id like "Q1", "Q2", etc.
- category: the section it belongs to (e.g. "Technical", "Pricing", "Compliance")
- question_text: the full text of the question or criterion
- question_type: "quantitative" if it expects a number/price/date/percentage, otherwise "qualitative"
- weight: importance weight 0-100. If not specified, distribute evenly.
- scoring_guidance: guidance on how to score it, or null

Return ONLY a valid JSON object with no explanation:
{
  "questions": [...],
  "categories": [list of unique category names]
}
"""


def _extract_content(response) -> str:
    """Extract text from response, handling thinking mode where content may be None."""
    msg = response.choices[0].message
    # Standard content
    if msg.content:
        return msg.content.strip()
    # Nemotron thinking mode puts answer in reasoning_content
    if hasattr(msg, "reasoning_content") and msg.reasoning_content:
        return msg.reasoning_content.strip()
    # Last resort: convert full message to string and find JSON
    return str(msg)


def _parse_json(raw: str) -> Dict:
    """Parse JSON from raw text, stripping markdown fences if present."""
    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Strip ```json ... ``` fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw.strip())
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Find first { ... } block
    match = re.search(r"(\{.*\})", raw, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    raise ValueError(f"Could not parse JSON from response: {raw[:500]}")


def extract_rfp_questions(document_text: str) -> Dict[str, Any]:
    """Extract structured questions from RFP document text using Nemotron."""
    truncated = document_text[:12000]

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "detailed thinking off"},
            {"role": "user", "content": f"{SYSTEM_PROMPT}\n\nExtract all evaluation questions from this RFP:\n\n{truncated}"},
        ],
        temperature=0.1,
        max_tokens=4096,
    )

    content = _extract_content(response)
    return _parse_json(content)
