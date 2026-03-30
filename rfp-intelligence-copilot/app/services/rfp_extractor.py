"""Uses LLM to extract structured questions from any RFP document."""
import os
import json
from openai import OpenAI
from typing import List, Dict, Any

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

SYSTEM_PROMPT = """
You are an expert procurement analyst. Your job is to extract all evaluation questions
from an RFP (Request for Proposal) document.

For each question or evaluation criterion, extract:
- question_id: sequential id like "Q1", "Q2", etc.
- category: the category or section it belongs to (e.g. "Technical", "Pricing", "Compliance")
- question_text: the full text of the question or criterion
- question_type: "quantitative" if it expects a number/price/date/percentage, otherwise "qualitative"
- weight: importance weight as a number 0-100. If not specified, distribute evenly.
- scoring_guidance: any guidance on how to score it, or null

Return a JSON object with:
{
  "questions": [...],
  "categories": [list of unique category names]
}

Only return valid JSON. No explanation.
"""


def extract_rfp_questions(document_text: str) -> Dict[str, Any]:
    """Extract structured questions from RFP document text using GPT-4o."""
    # Truncate to avoid token limits (keep first 12k chars)
    truncated = document_text[:12000]

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract all evaluation questions from this RFP:\n\n{truncated}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )

    result = json.loads(response.choices[0].message.content)
    return result
