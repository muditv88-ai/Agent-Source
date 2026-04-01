"""
orchestrator.py

Single entry-point router that maps an intent string to the correct
specialist agent and returns its result.

Called by:
  - /chat route (for action intents)
  - /communications route
  - Deadline scheduler
"""
from typing import Dict


INTENT_ROUTES: Dict[str, str] = {
    "generate_rfp":           "app.agents.rfp_generation_agent.RFPGenerationAgent",
    "send_communication":     "app.agents.comms_agent.CommsAgent",
    "run_analysis":           "app.agents.technical_analysis_agent.TechnicalAnalysisAgent",
    "price_comparison":       "app.agents.pricing_agent.PricingAgent",
    "award_scenario":         "app.agents.award_agent.AwardAgent",
    "onboard_supplier":       "app.agents.supplier_onboarding_agent.SupplierOnboardingAgent",
    "intake_response":        "app.agents.response_intake_agent.ResponseIntakeAgent",
    "chat":                   "app.agents.copilot_agent.CopilotAgent",
}


class OrchestratorAgent:
    """
    Routes incoming requests to the correct specialist agent.

    Usage:
        result = OrchestratorAgent().route("generate_rfp", {"mode": "generate", ...})
    """

    def route(self, intent: str, payload: dict) -> dict:
        module_path = INTENT_ROUTES.get(intent)
        if not module_path:
            raise ValueError(f"No agent registered for intent: '{intent}'")

        # Lazy import to avoid circular imports and keep startup fast
        module_name, class_name = module_path.rsplit(".", 1)
        import importlib
        module = importlib.import_module(module_name)
        AgentClass = getattr(module, class_name)

        agent = AgentClass()
        return agent.run(payload)
