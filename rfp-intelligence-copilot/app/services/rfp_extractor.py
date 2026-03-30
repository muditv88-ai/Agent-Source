"""Uses NVIDIA Nemotron to extract structured questions from any RFP document."""
import os
import json
from openai import OpenAI
from typing import Dict, Any

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)

MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

SYSTEM_PROMPT = """
You are an expert procurement analyst. Your job is to extract all evaluation questions
from an RFP (Request for Proposal) document.

For each question or evaluation criterion, extract:
- question_id: sequential id like "Q1", "Q2", etc.
- category: the category or section it belongs to (e.g. "Technical", "Pricing", "Compliance")
- question_text: the full text of the question or criterion
- question_type: "quantitative" if it expects a number/price/date/percentage, otherwise "qualitative"
- weight: importance weight as a number 0-100. If not specified, distribute evenly across all questions.
- scoring_guidance: any guidance on how to score it, or null

Return a JSON object with:
{
  "questions": [...],
  "categories": [list of unique category names]
}

Only return valid JSON. No explanation.
"""


def extract_rfp_questions(document_text: str) -> Dict[str, Any]:
    """Extract structured questions from RFP document text using Nemotron."""
    truncated = document_text[:12000]

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract all evaluation questions from this RFP:\n\n{truncated}"},
        ],
        temperature=0.1,
        max_tokens=4096,
    )

    content = response.choices[0].message.content.strip()
    # Strip markdown code fences if present
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content.strip())
