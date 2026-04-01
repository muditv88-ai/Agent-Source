"""
award_agent.py

Handles FM-8.1 through FM-8.6:
  - Execute award scenarios (wraps scenario_engine.py)
  - Generate narrative justification memo (LLM)
  - Send award / regret notifications (via CommsAgent)
  - Submit scenario for manager approval

scenario_engine.py is NOT modified — this agent wraps it.
"""
import os
import logging
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.agents.base_agent import BaseAgent, Tool
from app.services.scenario_engine import run_custom_scenario

logger = logging.getLogger(__name__)

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)
MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

NARRATIVE_PROMPT = """
You are a senior procurement analyst writing a formal award recommendation memo.

Scenario: {scenario_description}
Winning supplier(s) and allocation: {award_split}
Total cost: {total_cost}
Technical scores: {tech_scores}

Write a concise 3-paragraph justification:
  1. Why this scenario was selected over alternatives
  2. Key differentiators of the winning supplier(s)
  3. Recommended next steps

Professional tone, suitable for senior management review. Plain text, no headers.
"""


class AwardAgent(BaseAgent):
    """
    Award orchestration:
      run() → run scenario → generate narrative → (optionally) notify suppliers
    """

    def __init__(self):
        tools = [
            Tool(
                name="run_scenario",
                description="Execute an award scenario and return allocation + cost breakdown",
                fn=self._run_scenario,
                schema={"type": "object"},
            ),
            Tool(
                name="generate_narrative",
                description="Write a management-ready award justification memo",
                fn=self._generate_narrative,
                schema={"type": "object"},
            ),
            Tool(
                name="notify_suppliers",
                description="Send award and regret notifications to all suppliers",
                fn=self._notify_suppliers,
                schema={"type": "object"},
            ),
            Tool(
                name="submit_for_approval",
                description="Flag a scenario for manager approval",
                fn=self._submit_approval,
                schema={"type": "object"},
            ),
        ]
        super().__init__(tools)

    def _run_scenario(
        self, user_input: str, cost_model: dict, scenario_id: Optional[str] = None
    ) -> dict:
        """Directly delegates to the existing scenario_engine service."""
        return run_custom_scenario(user_input, cost_model, scenario_id)

    def _generate_narrative(
        self, scenario: dict, tech_scores: Optional[dict] = None
    ) -> str:
        prompt = NARRATIVE_PROMPT.format(
            scenario_description=scenario.get("description", "Award scenario"),
            award_split=scenario.get("award_split", {}),
            total_cost=scenario.get("total_cost", 0),
            tech_scores=tech_scores or {},
        )
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=600,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.error("Narrative generation failed: %s", e)
            return "Award narrative generation failed. Please draft manually."

    def _notify_suppliers(
        self,
        scenario: dict,
        all_suppliers: List[dict],
        project_id: str = "",
    ) -> Dict[str, dict]:
        from app.agents.comms_agent import CommsAgent

        comms = CommsAgent()
        results = {}
        awarded = set(scenario.get("award_split", {}).keys())

        for supplier in all_suppliers:
            name = supplier.get("name", "")
            notif_type = "award_notification" if name in awarded else "regret_notification"
            result = comms.run(
                {
                    "type": notif_type,
                    "supplier_name": name,
                    "recipient_email": supplier.get("email", ""),
                    "project_name": scenario.get("project_name", ""),
                    "project_id": project_id,
                    "award_value": scenario["award_split"].get(name, 0),
                    "awarded_items": scenario.get("awarded_items", {}).get(name, []),
                    "next_steps": "Our team will contact you to finalise the contract.",
                    "reason": "evaluation of all submitted proposals",
                    "auto_send": False,  # draft only — human approves before send
                }
            )
            results[name] = result
        return results

    def _submit_approval(
        self, scenario_id: str, approver_email: str, project_id: str = ""
    ) -> dict:
        from app.agents.comms_agent import CommsAgent

        CommsAgent().run(
            {
                "type": "rfp_invite",  # Reused as approval-request template
                "supplier_name": "Procurement Manager",
                "recipient_email": approver_email,
                "project_name": project_id,
                "deadline": "Please review at your earliest convenience",
                "portal_link": f"https://sourceiq.app/scenarios/{scenario_id}",
                "auto_send": False,
            }
        )
        return {"scenario_id": scenario_id, "approval_status": "pending_approval"}

    def run(self, input: dict, context: Optional[Dict] = None) -> dict:
        scenario = self._run_scenario(
            user_input=input.get("user_input", ""),
            cost_model=input.get("cost_model", {}),
        )
        narrative = self._generate_narrative(
            scenario=scenario,
            tech_scores=input.get("tech_scores", {}),
        )
        scenario["narrative"] = narrative
        scenario["approval_status"] = "draft"
        return scenario
