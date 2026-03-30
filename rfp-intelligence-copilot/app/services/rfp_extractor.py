"""Uses NVIDIA Nemotron to extract structured questions from any RFP document."""
import os
import json
import re
from openai import OpenAI
from typing import Dict, Any, List

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)

MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

SYSTEM_PROMPT = """
You are an expert procurement analyst. Extract all evaluation questions from an RFP document section.

For each question or evaluation criterion, extract:
- question_id: sequential id like "Q1", "Q2", etc. (continue from provided start index)
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
If there are no questions in this section, return {"questions": [], "categories": []}.
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


def _extract_from_chunk(chunk_text: str, start_q_index: int) -> Dict:
    """Call Nemotron on one chunk of text, with question IDs starting at start_q_index."""
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Note: Start question IDs from Q{start_q_index}.\n\n"
        f"Extract all evaluation questions from this RFP section:\n\n{chunk_text}"
    )
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "detailed thinking off"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    return _parse_json(_extract_content(response))


def _split_into_chunks(text: str, max_chars: int = 12000) -> List[str]:
    """Split text into chunks of max_chars, breaking on newlines."""
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


def extract_rfp_questions(document_text: str) -> Dict[str, Any]:
    """Extract structured questions from ALL sections of the RFP by processing chunk by chunk."""

    # Split on sheet boundaries first, then further chunk if needed
    sections = re.split(r"(?=^=== Sheet:)", document_text, flags=re.MULTILINE)
    sections = [s.strip() for s in sections if s.strip()]

    # If no sheet markers (PDF/DOCX), just chunk the whole text
    if not sections:
        sections = [document_text]

    all_questions: List[Dict] = []
    all_categories: set = set()
    q_counter = 1

    for section in sections:
        # Further chunk each section if it's too long
        chunks = _split_into_chunks(section, max_chars=12000)
        for chunk in chunks:
            if not chunk.strip():
                continue
            try:
                result = _extract_from_chunk(chunk, start_q_index=q_counter)
                questions = result.get("questions", [])
                # Re-assign question IDs sequentially to avoid duplicates across chunks
                for q in questions:
                    q["question_id"] = f"Q{q_counter}"
                    q_counter += 1
                all_questions.extend(questions)
                all_categories.update(result.get("categories", []))
            except Exception as e:
                # Log and continue — don't fail the whole parse because of one chunk
                print(f"Warning: chunk extraction failed: {e}")
                continue

    return {
        "questions": all_questions,
        "categories": sorted(all_categories),
    }
