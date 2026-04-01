"""
response_intake_agent.py

Handles FM-4.3 / FM-4.4 / FM-4.5:
  - Validate supplier response completeness vs RFP questions
  - Identify unanswered sections
  - Auto-draft clarification request via CommsAgent
  - Version tracking for resubmissions

Wraps existing supplier_parser.py — no changes to that service.
"""
import logging
from typing import Any, Dict, List, Optional

from app.agents.base_agent import BaseAgent, Tool
from app.services.supplier_parser import parse_supplier_response

logger = logging.getLogger(__name__)


class ResponseIntakeAgent(BaseAgent):
    """
    On each supplier response upload:
      1. Extract supplier name
      2. Map answers to RFP questions (via supplier_parser)
      3. Check completeness vs RFP requirements
      4. Auto-request missing sections via CommsAgent
      5. Version if resubmission detected
    """

    def __init__(self):
        tools = [
            Tool(
                name="parse_and_map_response",
                description="Parse supplier response text and map answers to RFP questions",
                fn=self._parse_and_map,
                schema={
                    "type": "object",
                    "properties": {
                        "file_text": {"type": "string"},
                        "rfp_questions": {"type": "array"},
                    },
                    "required": ["file_text", "rfp_questions"],
                },
            ),
            Tool(
                name="check_completeness",
                description="Check which RFP questions are unanswered in the parsed response",
                fn=self._check_completeness,
                schema={
                    "type": "object",
                    "properties": {
                        "parsed": {"type": "object"},
                        "rfp_questions": {"type": "array"},
                    },
                    "required": ["parsed", "rfp_questions"],
                },
            ),
        ]
        super().__init__(tools)

    def _parse_and_map(self, file_text: str, rfp_questions: list) -> dict:
        """Uses existing supplier_parser service — unchanged."""
        return parse_supplier_response(file_text, rfp_questions)

    def _check_completeness(self, parsed: dict, rfp_questions: list) -> dict:
        answered_ids = {
            q["question_id"]
            for q in parsed.get("answers", [])
            if str(q.get("answer", "")).strip()
        }
        all_ids = {q["question_id"] for q in rfp_questions}
        unanswered = all_ids - answered_ids
        completeness = round(len(answered_ids) / max(len(all_ids), 1) * 100, 1)
        return {
            "completeness_pct": completeness,
            "answered_count": len(answered_ids),
            "unanswered_ids": list(unanswered),
        }

    def run(self, input: dict, context: Optional[Dict] = None) -> dict:
        file_text = input["file_text"]
        rfp_questions = input["rfp_questions"]
        project_id = input.get("project_id", "")
        supplier_name = input.get("supplier_name", "")

        # Step 1: Parse
        parsed = self._parse_and_map(file_text, rfp_questions)
        if not supplier_name:
            supplier_name = parsed.get("supplier_name", "Unknown Supplier")

        # Step 2: Completeness check
        completeness = self._check_completeness(parsed, rfp_questions)

        # Step 3: Auto-request missing sections
        auto_followup = False
        if completeness["unanswered_ids"]:
            try:
                from app.agents.comms_agent import CommsAgent
                CommsAgent().run(
                    {
                        "type": "clarification_request",
                        "supplier_name": supplier_name,
                        "project_id": project_id,
                        "missing_questions": completeness["unanswered_ids"],
                        "auto_send": False,  # draft only
                    }
                )
                auto_followup = True
            except Exception as e:
                logger.warning("CommsAgent clarification failed: %s", e)

        return {
            "supplier_name": supplier_name,
            **completeness,
            "parsed_response": parsed,
            "auto_followup_drafted": auto_followup,
        }
