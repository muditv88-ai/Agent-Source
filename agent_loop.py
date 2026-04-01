"""
agent_loop.py
Core Plan → Execute → Observe → Respond orchestration loop.
Dispatches tasks to specialist agents and manages context state.
"""

import asyncio
import logging
from typing import Any
from agents.rfp_parser_agent import RFPParserAgent
# Future imports: SupplierAgent, ScoringAgent, etc.

logger = logging.getLogger(__name__)

AGENT_REGISTRY = {
    "rfp_parser": RFPParserAgent,
    # "supplier_matcher": SupplierMatcherAgent,
    # "scoring":          ScoringAgent,
    # "award":            AwardAgent,
}

class AgentLoop:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.context: dict[str, Any] = {}
        self.history: list[dict] = []

    async def run(self, task: str, payload: dict = {}) -> dict:
        """
        Main loop:  Plan → Execute → Observe → Respond
        """
        logger.info(f"[{self.session_id}] Task received: {task}")

        # ── 1. PLAN ──────────────────────────────────────────
        plan = self._plan(task, payload)
        logger.info(f"[{self.session_id}] Plan: {plan}")

        # ── 2. EXECUTE ───────────────────────────────────────
        results = {}
        for step in plan:
            agent_key = step["agent"]
            agent_cls = AGENT_REGISTRY.get(agent_key)
            if not agent_cls:
                logger.warning(f"Unknown agent: {agent_key}")
                continue
            agent = agent_cls(context=self.context)
            results[agent_key] = await agent.run(step["input"])

        # ── 3. OBSERVE ───────────────────────────────────────
        self.context.update(results)
        self.history.append({"task": task, "results": results})

        # ── 4. RESPOND ───────────────────────────────────────
        response = self._synthesise(results)
        logger.info(f"[{self.session_id}] Response ready.")
        return response

    # ── Private helpers ──────────────────────────────────────

    def _plan(self, task: str, payload: dict) -> list[dict]:
        """
        Naive task→agent routing.
        Replace with LLM-based planner when ready.
        """
        task_lower = task.lower()
        steps = []

        if "parse" in task_lower or "rfp" in task_lower:
            steps.append({"agent": "rfp_parser", "input": payload})

        if not steps:
            # Default: pass to rfp_parser as a catch-all during phase 2
            steps.append({"agent": "rfp_parser", "input": payload})

        return steps

    def _synthesise(self, results: dict) -> dict:
        return {
            "session_id": self.session_id,
            "status": "ok",
            "output": results,
        }
