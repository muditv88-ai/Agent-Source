"""Agentic chat service - answers questions and suggests scoring adjustments."""
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

SYSTEM_PROMPT = """
You are an expert procurement AI assistant embedded inside an RFP evaluation tool called ProcureIQ.

You have access to the current RFP analysis context including:
- The RFP questions and their categories/weights
- All supplier scores at question and category level
- Overall rankings

Your job is to:
1. Answer questions about the analysis (e.g. "Why did Supplier A score low on Technical?")
2. Accept feedback to adjust scoring (e.g. "Rescore Q3 for Supplier B as 8 because their answer was strong")
3. Accept weight changes (e.g. "Increase weight of Pricing category to 40%")
4. Explain scoring rationale
5. Suggest improvements to the evaluation

When the user wants to make a change, include a structured action in your response.

Always respond with a JSON object:
{
  "message": "your natural language response here",
  "action": null  // or one of the action objects below if user wants a change
}

Action types:
- Rescore a question:    {"type": "rescore", "supplier_name": "...", "question_id": "Q3", "new_score": 8.0, "reason": "..."}
- Adjust category weight: {"type": "adjust_weight", "category": "Pricing", "new_weight": 40}
- Exclude supplier:      {"type": "exclude_supplier", "supplier_name": "..."}
- Rerun analysis:        {"type": "rerun"}

Only include an action if the user is explicitly requesting a change. For questions/explanations, action is null.
Keep responses concise and professional.
"""


def _extract_content(response) -> str:
    msg = response.choices[0].message
    if msg.content:
        return msg.content.strip()
    if hasattr(msg, "reasoning_content") and msg.reasoning_content:
        return msg.reasoning_content.strip()
    return str(msg)


def _parse_response(raw: str) -> Dict:
    """Parse agent response, tolerating non-JSON conversational replies."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*\})", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Fallback: treat raw as a plain message
    return {"message": raw, "action": None}


def chat_with_agent(
    messages: List[Dict[str, str]],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Send a conversation to the agent with optional analysis context."""

    context_block = ""
    if context:
        context_block = f"\n\n=== CURRENT ANALYSIS CONTEXT ===\n{json.dumps(context, indent=2)}\n==================================\n"

    system_message = SYSTEM_PROMPT + context_block

    api_messages = [{"role": "system", "content": "detailed thinking off"},
                    {"role": "user", "content": system_message}]

    # Add conversation history
    for msg in messages:
        api_messages.append({"role": msg["role"], "content": msg["content"]})

    response = client.chat.completions.create(
        model=MODEL,
        messages=api_messages,
        temperature=0.3,
        max_tokens=1024,
    )

    return _parse_response(_extract_content(response))
