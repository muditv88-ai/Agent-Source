"""Parses supplier response documents and maps answers to RFP questions."""
import os
import json
from openai import OpenAI
from typing import List, Dict, Any

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

SYSTEM_PROMPT = """
You are an expert procurement analyst. Given an RFP's list of questions and a supplier's
response document, extract each supplier's answer to each question.

Return a JSON object:
{
  "supplier_name": "name of supplier if identifiable, else 'Unknown Supplier'",
  "answers": {
    "Q1": "supplier's answer to question 1",
    "Q2": "supplier's answer to question 2",
    ...
  }
}

If a question is not answered, use "No response provided".
Only return valid JSON. No explanation.
"""


def extract_supplier_answers(
    supplier_document_text: str,
    questions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Map supplier document answers to RFP questions using GPT-4o."""
    questions_summary = "\n".join(
        f"{q['question_id']}: {q['question_text']}" for q in questions
    )
    truncated_doc = supplier_document_text[:10000]

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"RFP Questions:\n{questions_summary}\n\n"
                    f"Supplier Response Document:\n{truncated_doc}"
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )

    return json.loads(response.choices[0].message.content)
