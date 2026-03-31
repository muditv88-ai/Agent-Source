"""
chat_agent.py v2

Agentic chat — answers questions, applies scoring adjustments,
AND now handles pricing award scenario requests via the chatbot.

Routing logic:
  1. Pricing scenario detected → route to scenario_engine.run_custom_scenario()
  2. Scoring/analysis question → handled by LLM with context
  3. Other → general procurement advice
"""
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

# ── Keyword triggers for pricing scenario routing ─────────────────────────────
_PRICING_TRIGGERS = [
    "award", "scenario", "split", "allocat", "basket", "l1", "l2",
    "cheapest", "lowest price", "best price", "give to", "assign",
    "exclude supplier", "only use", "use only", "percent", "%", "portion",
    "category to", "all items", "above $", "below $",
]

SYSTEM_PROMPT = """
You are an expert procurement AI assistant embedded inside an RFP evaluation tool called ProcureIQ.

You have access to the current RFP analysis context including:
- The RFP questions and their categories/weights
- All supplier scores at question and category level
- Overall rankings
- Pricing analysis: SKU-level price matrix, L1/L2 suppliers, award scenarios

Your job is to:
1. Answer questions about the analysis (e.g. "Why did Supplier A score low on Technical?")
2. Accept feedback to adjust scoring (e.g. "Rescore Q3 for Supplier B as 8")
3. Accept weight changes (e.g. "Increase weight of Pricing category to 40%")
4. Explain pricing differences between suppliers (e.g. "Where is Supplier A cheapest?")
5. Build custom award scenarios (e.g. "Award logistics to Supplier A, rest to cheapest")
6. Suggest improvements to the evaluation

For pricing scenario requests, describe what the scenario does and its estimated total cost.

Always respond with a JSON object:
{
  "message": "your natural language response",
  "action": null
}

Action types:
- Rescore a question:      {"type": "rescore",         "supplier_name": "...", "question_id": "Q3", "new_score": 8.0, "reason": "..."}
- Adjust category weight:  {"type": "adjust_weight",   "category": "Pricing", "new_weight": 40}
- Exclude supplier:        {"type": "exclude_supplier", "supplier_name": "..."}
- Run pricing scenario:    {"type": "pricing_scenario", "user_input": "<exact user text>", "granularity": "sku|category"}
- Rerun analysis:          {"type": "rerun"}

Only include an action if the user is explicitly requesting a change or scenario.
For questions/explanations, action is null.
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
    return {"message": raw, "action": None}


def _is_pricing_scenario_request(text: str) -> bool:
    """Quick keyword check before sending to LLM routing."""
    t = text.lower()
    return any(trigger in t for trigger in _PRICING_TRIGGERS)


def chat_with_agent(
    messages: List[Dict[str, str]],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Send a conversation to the agent with optional analysis context.
    Context may include: analysis results, pricing cost_model, supplier list.
    """
    context_block = ""
    if context:
        # Summarise pricing context if present (avoid huge payloads)
        ctx_summary = {}
        if "suppliers" in context:
            ctx_summary["suppliers"] = context["suppliers"]
        if "total_costs" in context:
            ctx_summary["l1_l2_ranking"] = [
                {"label": r.get("label"), "supplier": r["supplier_name"], "total": r["total_cost"]}
                for r in context.get("total_costs", [])
            ]
        if "categories" in context:
            ctx_summary["categories"] = context["categories"]
        if "category_matrix" in context:
            ctx_summary["category_totals"] = context["category_matrix"]
        # Include full context for scoring if available
        for k in ("rfp_id", "category_scores", "award_recommendation"):
            if k in context:
                ctx_summary[k] = context[k]
        context_block = f"\n\n=== CURRENT CONTEXT ===\n{json.dumps(ctx_summary, indent=2)}\n=======================\n"

    # --- Pricing scenario fast-path ---
    last_user_msg = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )
    if _is_pricing_scenario_request(last_user_msg) and context and context.get("cost_model"):
        try:
            from app.services.scenario_engine import run_custom_scenario
            scenario_result = run_custom_scenario(
                user_input = last_user_msg,
                cost_model = context["cost_model"],
            )
            total = scenario_result.get("total_cost", 0)
            split = scenario_result.get("award_split", {})
            split_str = ", ".join(f"{s}: {v:,.2f}" for s, v in split.items())
            return {
                "message": (
                    f"I've built your custom scenario.\n\n"
                    f"**Total Cost**: {total:,.2f}\n"
                    f"**Award Split**: {split_str}\n\n"
                    f"The full breakdown is available in the Pricing tab under Custom Scenarios."
                ),
                "action": {
                    "type":    "pricing_scenario",
                    "result":  scenario_result,
                },
            }
        except Exception as e:
            pass  # fall through to LLM

    # --- Standard LLM path ---
    system_message = SYSTEM_PROMPT + context_block
    api_messages   = [
        {"role": "system", "content": "detailed thinking off"},
        {"role": "user",   "content": system_message},
    ]
    for msg in messages:
        api_messages.append({"role": msg["role"], "content": msg["content"]})

    response = client.chat.completions.create(
        model=MODEL,
        messages=api_messages,
        temperature=0.3,
        max_tokens=1024,
    )
    return _parse_response(_extract_content(response))
