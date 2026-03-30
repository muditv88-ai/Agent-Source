"""Parses supplier response documents and maps answers to RFP questions."""
import os
import json
import re
from openai import OpenAI
from typing import List, Dict, Any, Optional

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)

MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

# Smaller chunk size so the model has enough token budget left for its JSON output
CHUNK_MAX_CHARS = 6000
# Max questions to ask about per API call (limits prompt+output size)
QUESTIONS_PER_CALL = 20

SYSTEM_PROMPT = """
You are an expert procurement analyst. Given an RFP's list of questions and a section of a
supplier's response document, extract the supplier's answer to each question.

Return ONLY a valid JSON object, no explanation or prose:
{
  "supplier_name": "name of supplier if identifiable, else 'Unknown Supplier'",
  "answers": {
    "Q1": "supplier's answer",
    "Q2": "supplier's answer"
  }
}

Rules:
- Only include questions that have answers in THIS section.
- If a question is not answered here, omit it entirely.
- Be concise in answers - a few sentences max per question.
- Always close every JSON brace and bracket properly.
"""


def _extract_content(response) -> str:
    msg = response.choices[0].message
    if msg.content:
        return msg.content.strip()
    if hasattr(msg, "reasoning_content") and msg.reasoning_content:
        return msg.reasoning_content.strip()
    return str(msg)


def _repair_json(raw: str) -> str:
    """
    Attempt to close a truncated/malformed JSON string by balancing
    open braces, brackets, and unterminated strings.
    """
    # Strip markdown code fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw.strip())

    # Track what's open
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

    # Close any open string
    if in_string:
        repaired.append('"')

    # Close any open structures in reverse order
    for closer in reversed(stack):
        repaired.append(closer)

    return "".join(repaired)


def _parse_json(raw: str) -> Dict:
    """Parse LLM JSON output with progressive fallback and repair."""
    # Pass 1: direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Pass 2: strip markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Pass 3: extract outermost {...} block
    match = re.search(r"(\{.*)", cleaned, re.DOTALL)
    if match:
        candidate = match.group(1)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # Pass 4: repair truncated JSON
        try:
            repaired = _repair_json(candidate)
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from response: {raw[:300]}")


def _split_into_chunks(text: str, max_chars: int = CHUNK_MAX_CHARS) -> List[str]:
    """Split text into chunks, preferring newline boundaries."""
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


def _call_llm_for_chunk(
    chunk: str,
    questions: List[Dict[str, Any]],
    current_supplier_name: str,
) -> Dict:
    """Call the LLM for one chunk with a subset of questions."""
    questions_summary = "\n".join(
        f"{q['question_id']}: {q['question_text']}" for q in questions
    )
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Supplier name known so far: {current_supplier_name}\n\n"
        f"RFP Questions to look for in this section:\n{questions_summary}\n\n"
        f"Supplier Response Section:\n{chunk}"
    )
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "detailed thinking off"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=3000,  # capped to leave headroom and prevent runaway output
    )
    return _parse_json(_extract_content(response))


def extract_supplier_answers(
    supplier_document_text: str,
    questions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Map supplier document answers to ALL RFP questions.

    Strategy to prevent data loss on large files:
    1. Split document on sheet boundaries, then further chunk large sections.
    2. Batch questions into groups of QUESTIONS_PER_CALL to keep prompts small.
    3. For each (chunk x question_batch) pair, call the LLM.
    4. Use partial JSON repair so truncated responses still yield partial data.
    5. Merge all answers across all calls.
    """
    # Split on sheet boundaries first
    sections = re.split(r"(?=^=== Sheet:)", supplier_document_text, flags=re.MULTILINE)
    sections = [s.strip() for s in sections if s.strip()]
    if not sections:
        sections = [supplier_document_text]

    merged_answers: Dict[str, str] = {}
    supplier_name = "Unknown Supplier"

    # Batch questions so each API call handles at most QUESTIONS_PER_CALL
    question_batches = [
        questions[i: i + QUESTIONS_PER_CALL]
        for i in range(0, len(questions), QUESTIONS_PER_CALL)
    ]

    for section in sections:
        chunks = _split_into_chunks(section, max_chars=CHUNK_MAX_CHARS)
        for chunk in chunks:
            if not chunk.strip():
                continue
            for q_batch in question_batches:
                # Skip question batches whose IDs are already fully answered
                unanswered = [
                    q for q in q_batch
                    if merged_answers.get(q["question_id"], "No response provided")
                    == "No response provided"
                ]
                if not unanswered:
                    continue  # all questions in this batch answered, skip call

                try:
                    result = _call_llm_for_chunk(chunk, unanswered, supplier_name)

                    if supplier_name == "Unknown Supplier" and result.get("supplier_name"):
                        supplier_name = result["supplier_name"]

                    for qid, answer in result.get("answers", {}).items():
                        if (
                            answer
                            and answer.strip()
                            and answer.strip().lower() != "no response provided"
                            and (
                                qid not in merged_answers
                                or merged_answers[qid] == "No response provided"
                            )
                        ):
                            merged_answers[qid] = answer.strip()

                except Exception as e:
                    print(f"Warning: chunk/batch extraction failed: {e}")
                    continue  # non-fatal — other chunks/batches will still run

    # Fill in any questions never answered across all calls
    for q in questions:
        if q["question_id"] not in merged_answers:
            merged_answers[q["question_id"]] = "No response provided"

    return {
        "supplier_name": supplier_name,
        "answers": merged_answers,
    }
