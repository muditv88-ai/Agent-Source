"""Uses Gemini to extract structured questions from any RFP document."""
import os
import json
import google.generativeai as genai
from typing import Dict, Any

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
_model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config=genai.GenerationConfig(
        response_mime_type="application/json",
        temperature=0.1,
    ),
)

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

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Extract all evaluation questions from this RFP:\n\n{truncated}"
    )

    response = _model.generate_content(prompt)
    result = json.loads(response.text)
    return result
