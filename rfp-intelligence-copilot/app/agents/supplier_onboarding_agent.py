"""
supplier_onboarding_agent.py

Handles FM-3.1 / FM-3.2:
  - Send onboarding invitations
  - Validate submitted documents against checklist
  - Auto-request missing documents via CommsAgent
  - Track onboarding status per supplier
"""
import logging
from typing import Any, Dict, List, Optional

from app.agents.base_agent import BaseAgent, Tool

logger = logging.getLogger(__name__)

ONBOARDING_CHECKLIST = [
    "company_registration",
    "tax_id",
    "bank_details",
    "iso_certification",
    "insurance_certificate",
    "contact_details",
]

# In-memory supplier store (replace with DB in production)
_supplier_store: Dict[str, dict] = {}


class SupplierOnboardingAgent(BaseAgent):
    """
    Agentic onboarding flow:
      1. send_onboarding_invite  → email invite to supplier portal
      2. validate_onboarding_docs → check uploaded docs vs checklist
         (if incomplete → auto-trigger request_missing_docs)
      3. approve / reject supplier
    """

    def __init__(self):
        tools = [
            Tool(
                name="send_onboarding_invite",
                description="Send portal onboarding invitation to a new supplier",
                fn=self._send_invite,
                schema={
                    "type": "object",
                    "properties": {
                        "supplier_email": {"type": "string"},
                        "supplier_name": {"type": "string"},
                        "project_id": {"type": "string"},
                        "portal_link": {"type": "string"},
                    },
                    "required": ["supplier_email", "supplier_name"],
                },
            ),
            Tool(
                name="validate_onboarding_docs",
                description="Check supplier-uploaded documents against the onboarding checklist",
                fn=self._validate_docs,
                schema={
                    "type": "object",
                    "properties": {
                        "supplier_id": {"type": "string"},
                        "uploaded_docs": {
                            "type": "array",
                            "items": {"type": "object"},
                        },
                    },
                    "required": ["supplier_id", "uploaded_docs"],
                },
            ),
            Tool(
                name="request_missing_docs",
                description="Email supplier requesting specific missing documents",
                fn=self._request_missing,
                schema={
                    "type": "object",
                    "properties": {
                        "supplier_id": {"type": "string"},
                        "missing_items": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["supplier_id", "missing_items"],
                },
            ),
        ]
        super().__init__(tools)

    def _send_invite(
        self,
        supplier_email: str,
        supplier_name: str,
        project_id: str = "",
        portal_link: str = "https://sourceiq.app/onboard",
    ) -> dict:
        from app.agents.comms_agent import CommsAgent

        result = CommsAgent().run(
            {
                "type": "onboarding_invite",
                "supplier_name": supplier_name,
                "recipient_email": supplier_email,
                "project_id": project_id,
                "portal_link": portal_link,
                "auto_send": False,  # draft only — human reviews before send
            }
        )
        # Register supplier in store
        _supplier_store[supplier_email] = {
            "supplier_name": supplier_name,
            "email": supplier_email,
            "project_id": project_id,
            "status": "invited",
        }
        return {"invite_result": result, "supplier_status": "invited"}

    def _validate_docs(
        self, supplier_id: str, uploaded_docs: List[dict]
    ) -> dict:
        provided = {doc.get("doc_type", "") for doc in uploaded_docs}
        missing = [item for item in ONBOARDING_CHECKLIST if item not in provided]
        score = round(
            (len(ONBOARDING_CHECKLIST) - len(missing)) / len(ONBOARDING_CHECKLIST) * 100, 1
        )
        status = "approved" if not missing else "pending_docs"

        if supplier_id in _supplier_store:
            _supplier_store[supplier_id]["status"] = status

        return {
            "supplier_id": supplier_id,
            "completeness_score": score,
            "missing": missing,
            "provided": list(provided),
            "status": status,
        }

    def _request_missing(
        self, supplier_id: str, missing_items: List[str]
    ) -> dict:
        from app.agents.comms_agent import CommsAgent

        supplier = _supplier_store.get(supplier_id, {})
        return CommsAgent().run(
            {
                "type": "missing_docs_request",
                "supplier_name": supplier.get("supplier_name", supplier_id),
                "recipient_email": supplier.get("email", ""),
                "supplier_id": supplier_id,
                "missing_items": missing_items,
                "auto_send": False,
            }
        )

    def run(self, input: dict, context: Optional[Dict] = None) -> dict:
        step = input.get("step", "invite")

        if step == "invite":
            return self._send_invite(
                supplier_email=input["supplier_email"],
                supplier_name=input["supplier_name"],
                project_id=input.get("project_id", ""),
                portal_link=input.get("portal_link", "https://sourceiq.app/onboard"),
            )

        elif step == "validate":
            result = self._validate_docs(
                supplier_id=input["supplier_id"],
                uploaded_docs=input.get("uploaded_docs", []),
            )
            # Agent auto-triggers clarification for missing docs
            if result["missing"]:
                self._request_missing(input["supplier_id"], result["missing"])
                result["auto_followup_sent"] = True
            return result

        return {"error": f"Unknown step: {step}"}
