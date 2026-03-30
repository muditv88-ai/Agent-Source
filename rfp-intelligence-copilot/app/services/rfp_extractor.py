"""Uses Gemini to extract structured questions from any RFP document."""
import os
import json
from google import genai
from google.genai import types
from typing import Dict, Any

_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

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
    """Extract structured questions from RFP document text using Gemini."""
    truncated = document_text[:12000]

    response = _client.models.generate_content(
        model="gemini-2.0-flash",
        contents=f"{SYSTEM_PROMPT}\n\nExtract all evaluation questions from this RFP:\n\n{truncated}",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    return json.loads(response.text)
