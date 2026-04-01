"""
copilot_agent.py

Handles FM-9.1 through FM-9.5:
  Full replacement of chat_agent.py's keyword-routing pattern with
  proper OpenAI function-calling.

  Each specialist agent is exposed as an LLM-callable tool.
  Existing chat_agent.chat_with_agent() is preserved as a fallback
  for models that do not support function-calling.

Flow:
  1. First LLM call → may return a tool_call or a direct message
  2. If tool_call → execute the tool → second LLM call to summarise result
  3. Return {message, action}

v3.1: Respects feature flags 'function_calling' and 'chatbot_actions'.
"""
import os
import json
import logging
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.agents.base_agent import BaseAgent, Tool

logger = logging.getLogger(__name__)

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)
MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

SYSTEM_PROMPT = """
You are SourceIQ Copilot, an expert procurement AI assistant.
You have access to tools to:
  - generate or parse RFP documents
  - draft and send communications to suppliers
  - run award scenarios and generate justification memos
  - summarise technical analysis scores

Use tools when the user requests an action.
For questions and explanations, answer directly using the provided project context.
Always be concise and professional.
"""


class CopilotAgent(BaseAgent):
    """
    Conversational procurement agent with function-calling.
    Falls back to chat_agent.chat_with_agent() if:
      - tool_calls are not returned by the model, OR
      - the 'function_calling' feature flag is disabled for the project.
    """

    def __init__(self):
        tools = [
            Tool(
                name="generate_rfp",
                description="Generate or parse an RFP document. Use for: 'create an RFP', 'generate RFP for...', 'parse this RFP'.",
                fn=lambda **kw: self._delegate("app.agents.rfp_generation_agent", "RFPGenerationAgent", kw),
                schema={
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["generate", "upload"]},
                        "category": {"type": "string"},
                        "scope": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="send_communication",
                description="Draft or send a procurement email to a supplier. Use for: 'send email', 'draft invite', 'notify supplier'.",
                fn=lambda **kw: self._delegate("app.agents.comms_agent", "CommsAgent", kw),
                schema={
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "supplier_name": {"type": "string"},
                        "project_name": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="run_award_scenario",
                description="Execute and explain an award scenario. Use for: 'award scenario', 'who should we award', 'split the award'.",
                fn=lambda **kw: self._delegate("app.agents.award_agent", "AwardAgent", kw),
                schema={
                    "type": "object",
                    "properties": {
                        "user_input": {"type": "string"},
                        "cost_model": {"type": "object"},
                    },
                },
            ),
            Tool(
                name="get_analysis_summary",
                description="Return a technical analysis summary for suppliers. Use for: 'show scores', 'who scored highest', 'technical evaluation'.",
                fn=lambda **kw: self._delegate("app.agents.technical_analysis_agent", "TechnicalAnalysisAgent", kw),
                schema={
                    "type": "object",
                    "properties": {
                        "questions": {"type": "array"},
                        "supplier_responses": {"type": "object"},
                    },
                },
            ),
        ]
        super().__init__(tools)

    def _delegate(self, module_name: str, class_name: str, payload: dict) -> dict:
        """Lazy-load and run a specialist agent."""
        import importlib
        module = importlib.import_module(module_name)
        AgentClass = getattr(module, class_name)
        return AgentClass().run(payload)

    def run(self, input: dict, context: Optional[Dict] = None) -> dict:
        messages = input.get("messages", [])
        project_context = input.get("context", {})
        project_id = input.get("project_id", "")

        # --- Feature flag checks ---
        function_calling_enabled = True
        chatbot_actions_enabled = True
        if project_id:
            try:
                from app.services.feature_flags import flag_enabled
                function_calling_enabled = flag_enabled(project_id, "function_calling")
                chatbot_actions_enabled = flag_enabled(project_id, "chatbot_actions")
            except Exception:
                pass  # flag service unavailable — default to enabled

        # If actions are disabled, strip all tools so the LLM answers conversationally only
        if not chatbot_actions_enabled:
            self.tools = {}

        # If function-calling is disabled, skip directly to fallback
        if not function_calling_enabled:
            logger.info("function_calling flag off for project %s — using chat_agent fallback", project_id)
            from app.services.chat_agent import chat_with_agent
            return chat_with_agent(messages, project_context)

        # Build context string for system prompt
        ctx_str = ""
        if project_context:
            ctx_str = f"\n\nProject context: {json.dumps(project_context, indent=2)}"

        api_messages = [
            {"role": "system", "content": SYSTEM_PROMPT + ctx_str},
            *[{"role": m["role"] if isinstance(m, dict) else m.role,
               "content": m["content"] if isinstance(m, dict) else m.content}
              for m in messages],
        ]

        try:
            call_kwargs: dict = {
                "model": MODEL,
                "messages": api_messages,
                "temperature": 0.3,
                "max_tokens": 1024,
            }
            if self.tools:  # only pass tools if any are active
                call_kwargs["tools"] = self._tool_schemas()
                call_kwargs["tool_choice"] = "auto"

            response = self._call_with_retry(
                client.chat.completions.create, **call_kwargs
            )
            msg = response.choices[0].message

            if msg.tool_calls and self.tools:
                tc = msg.tool_calls[0]
                tool_name = tc.function.name
                tool_args = json.loads(tc.function.arguments)

                if tool_name not in self.tools:
                    logger.warning("Unknown tool called: %s", tool_name)
                    return {"message": msg.content or "", "action": None}

                tool_result = self.tools[tool_name].call(**tool_args)

                # Second LLM call: summarise tool result in natural language
                api_messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tool_name, "arguments": tc.function.arguments},
                    }],
                })
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(tool_result),
                })
                final_resp = self._call_with_retry(
                    client.chat.completions.create,
                    model=MODEL,
                    messages=api_messages,
                    temperature=0.3,
                    max_tokens=512,
                )
                return {
                    "message": final_resp.choices[0].message.content or "",
                    "action": {"type": tool_name, "result": tool_result},
                }

            return {"message": msg.content or "", "action": None}

        except Exception as e:
            logger.error("CopilotAgent LLM call failed: %s — falling back to chat_agent", e)
            from app.services.chat_agent import chat_with_agent
            return chat_with_agent(messages, project_context)
