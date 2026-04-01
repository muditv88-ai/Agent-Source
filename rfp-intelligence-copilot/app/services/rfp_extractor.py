"""Uses NVIDIA Nemotron to extract structured questions from any RFP document."""
import os
import json
import re
import hashlib
import concurrent.futures
from openai import OpenAI
from typing import Dict, Any, List

# ── Lazy client initialisation ────────────────────────────────────────────────
# Do NOT instantiate OpenAI at module level.  The key may not be present when
# the module is first imported (e.g. during uvicorn startup), which would raise
# OpenAIError and crash the whole process.  Instead, create the client on first
# use via _get_client().
_client: OpenAI | None = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("NVIDIA_API_KEY")
        if not api_key:
            raise RuntimeError(
                "NVIDIA_API_KEY environment variable is not set. "
                "Export it before starting the server."
            )
        _client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=api_key,
        )
    return _client

MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"
CHUNK_MAX_CHARS = 8000    # smaller chunks = faster per-call latency
MAX_WORKERS = 10          # more parallel calls to compensate

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
    response = _get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "detailed thinking off"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=8192,
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


def _file_hash(text: str) -> str:
    """MD5 of the full document text — used as cache key."""
    return hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()


def extract_rfp_questions(document_text: str, cache_dir: str = None) -> Dict[str, Any]:
    """
    Extract structured questions from ALL sections of the RFP.
    Chunks are processed in parallel to handle large documents quickly.

    If cache_dir is provided, results are cached by document hash so
    re-parsing the same file is instant.
    """
    # ── Cache check ──────────────────────────────────────────────────────────
    doc_hash = _file_hash(document_text)
    if cache_dir:
        from pathlib import Path
        cache_path = Path(cache_dir) / f"parse_cache_{doc_hash}.json"
        if cache_path.exists():
            print(f"[rfp_extractor] cache HIT for hash {doc_hash[:8]} — skipping LLM calls")
            return json.loads(cache_path.read_text())

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
    output = {
        "questions": all_questions,
        "categories": sorted(all_categories),
    }

    # ── Cache write ──────────────────────────────────────────────────────────
    if cache_dir:
        cache_path.write_text(json.dumps(output, indent=2))
        print(f"[rfp_extractor] cache WRITE for hash {doc_hash[:8]}")

    return output
