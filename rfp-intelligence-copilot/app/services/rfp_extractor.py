"""Uses NVIDIA Nemotron to extract structured questions from any RFP document."""
import os
import json
import re
import concurrent.futures
from openai import OpenAI
from typing import Dict, Any, List

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)

MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"
CHUNK_MAX_CHARS = 20000   # larger chunks = fewer LLM calls
MAX_WORKERS = 6           # parallel chunk processing

SYSTEM_PROMPT = """
You are an expert procurement analyst. Extract all evaluation questions from an RFP document section.

For each question or evaluation criterion, extract:
- question_id: sequential id like "Q1", "Q2", etc. (continue from provided start index)
- category: the section it belongs to (e.g. "Technical", "Pricing", "Compliance")
- question_text: the full text of the question or criterion
- question_type: "quantitative" if it expects a number/price/date/percentage, otherwise "qualitative"
- weight: importance weight 0-100. If not specified, distribute evenly across all questions.
- scoring_guidance: guidance on how to score it, or null

Return ONLY a valid JSON object with no explanation:
{
  "questions": [...],
  "categories": [list of unique category names]
}
If there are no questions in this section, return {"questions": [], "categories": []}.
Always close every JSON brace and bracket properly.
"""


def _extract_content(response) -> str:
    msg = response.choices[0].message
    if msg.content:
        return msg.content.strip()
    if hasattr(msg, "reasoning_content") and msg.reasoning_content:
        return msg.reasoning_content.strip()
    return str(msg)


def _repair_json(raw: str) -> str:
    """Close unclosed braces/brackets/strings in truncated JSON."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw.strip())
    stack = []
    in_string = False
    escaped = False
    repaired = []
    for char in raw:
        if escaped:
            escaped = False
            repaired.append(char)
            continue
        if char == "\\" and in_string:
            escaped = True
            repaired.append(char)
            continue
        if char == '"':
            in_string = not in_string
        elif not in_string:
            if char in ('{', '['):
                stack.append('}' if char == '{' else ']')
            elif char in ('}', ']'):
                if stack and stack[-1] == char:
                    stack.pop()
        repaired.append(char)
    if in_string:
        repaired.append('"')
    for closer in reversed(stack):
        repaired.append(closer)
    return "".join(repaired)


def _parse_json(raw: str) -> Dict:
    for attempt in (raw, re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE).rstrip("`")):
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            pass
    match = re.search(r"(\{.*)", raw, re.DOTALL)
    if match:
        candidate = match.group(1)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(_repair_json(candidate))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not parse JSON: {raw[:300]}")


def _extract_from_chunk(chunk_text: str, chunk_index: int) -> Dict:
    """Call LLM on one chunk. Returns raw questions (IDs reassigned later)."""
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
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
    result = _parse_json(_extract_content(response))
    print(f"[rfp_extractor] chunk {chunk_index}: {len(result.get('questions', []))} questions found")
    return result


def _split_into_chunks(text: str, max_chars: int = CHUNK_MAX_CHARS) -> List[str]:
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
    """
    Extract structured questions from ALL sections of the RFP.
    Chunks are processed in parallel to handle large documents quickly.
    """
    sections = re.split(r"(?=^=== Sheet:)", document_text, flags=re.MULTILINE)
    sections = [s.strip() for s in sections if s.strip()]
    if not sections:
        sections = [document_text]

    # Build flat list of all chunks across all sections
    all_chunks: List[str] = []
    for section in sections:
        all_chunks.extend(_split_into_chunks(section, max_chars=CHUNK_MAX_CHARS))
    all_chunks = [c for c in all_chunks if c.strip()]

    print(f"[rfp_extractor] processing {len(all_chunks)} chunks in parallel (max_workers={MAX_WORKERS})")

    # Process all chunks in parallel
    chunk_results: List[Dict] = [None] * len(all_chunks)
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_extract_from_chunk, chunk, i): i
            for i, chunk in enumerate(all_chunks)
        }
        for future in concurrent.futures.as_completed(futures):
            i = futures[future]
            try:
                chunk_results[i] = future.result()
            except Exception as e:
                print(f"[rfp_extractor] chunk {i} failed: {e}")
                chunk_results[i] = {"questions": [], "categories": []}

    # Merge results, reassign sequential IDs
    all_questions: List[Dict] = []
    all_categories: set = set()
    q_counter = 1

    for result in chunk_results:
        if result is None:
            continue
        for q in result.get("questions", []):
            q["question_id"] = f"Q{q_counter}"
            q_counter += 1
            all_questions.append(q)
        all_categories.update(result.get("categories", []))

    print(f"[rfp_extractor] total questions extracted: {len(all_questions)}")
    return {
        "questions": all_questions,
        "categories": sorted(all_categories),
    }
