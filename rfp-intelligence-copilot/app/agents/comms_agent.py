"""
comms_agent.py

Full replacement/expansion of communication_engine.py.

Handles FM-5.1 through FM-5.6:
  - LLM-drafted contextual emails (RFP invite, deadline reminder,
    clarification request, award/regret notifications, onboarding invite,
    missing docs request)
  - Optional SMTP send (set auto_send=True + configure SMTP env vars)
  - Communication log per project

The existing draft_clarification_email() in communication_engine.py is
preserved and still works; this agent extends it with additional templates
and real send capability.
"""
import os
import re
import json
import logging
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.agents.base_agent import BaseAgent, Tool

logger = logging.getLogger(__name__)

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)
MODEL = "meta/llama-3.3-70b-instruct"  # Faster model — good for comms drafts

EMAIL_TEMPLATES: Dict[str, str] = {
    "rfp_invite": """
Draft a professional RFP invitation email.
Project: {project_name}
Supplier: {supplier_name}
Submission deadline: {deadline}
Portal link: {portal_link}
Tone: professional, concise. Include deadline prominently.
Return JSON only: {{"subject": "...", "body": "..."}}
""",
    "deadline_reminder": """
Draft a deadline reminder email.
Supplier: {supplier_name}, Project: {project_name}
Deadline: {deadline}, Days remaining: {days_remaining}
Current status: {response_status}
Return JSON only: {{"subject": "...", "body": "..."}}
""",
    "clarification_request": """
Draft a clarification request email for missing RFP response sections.
Supplier: {supplier_name}, Project ID: {project_id}
Missing sections / questions: {missing_questions}
Tone: polite, specific. Reference each missing item.
Return JSON only: {{"subject": "...", "body": "..."}}
""",
    "award_notification": """
Draft an award notification letter.
Supplier: {supplier_name}, Project: {project_name}
Award value: {award_value}, Awarded items: {awarded_items}
Next steps: {next_steps}
Return JSON only: {{"subject": "...", "body": "..."}}
""",
    "regret_notification": """
Draft a professional regret / non-award letter.
Supplier: {supplier_name}, Project: {project_name}
Brief reason: {reason}
Tone: respectful, leave door open for future business.
Return JSON only: {{"subject": "...", "body": "..."}}
""",
    "onboarding_invite": """
Draft a supplier onboarding invitation email.
Supplier: {supplier_name}
Portal link: {portal_link}
Documents required: company registration, tax ID, bank details, ISO/quality certifications, insurance certificate.
Return JSON only: {{"subject": "...", "body": "..."}}
""",
    "missing_docs_request": """
Draft a follow-up email requesting specific missing onboarding documents.
Supplier: {supplier_name}, Supplier ID: {supplier_id}
Missing documents: {missing_items}
Tone: friendly but firm. List each missing document clearly.
Return JSON only: {{"subject": "...", "body": "..."}}
""",
}

# In-memory communication log (replace with DB in production)
_comm_log: Dict[str, List[dict]] = {}


def _fill_template(template: str, params: dict) -> str:
    """Safe format — missing keys replaced with empty string."""
    import string

    class SafeDict(dict):
        def __missing__(self, key):
            return ""

    return template.format_map(SafeDict(params))


class CommsAgent(BaseAgent):
    """Drafts and (optionally) sends procurement communications via LLM."""

    def __init__(self):
        tools = [
            Tool(
                name="draft_email",
                description="Draft an email using LLM for a given template type",
                fn=self._draft_email,
                schema={
                    "type": "object",
                    "properties": {
                        "template_type": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["template_type", "params"],
                },
            ),
            Tool(
                name="send_email",
                description="Send an email via SMTP",
                fn=self._send_email,
                schema={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
            ),
            Tool(
                name="log_communication",
                description="Log a sent communication to the project communication trail",
                fn=self._log_comm,
                schema={
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "supplier_id": {"type": "string"},
                        "email_type": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                },
            ),
        ]
        super().__init__(tools)

    def _draft_email(self, template_type: str, params: dict) -> dict:
        template = EMAIL_TEMPLATES.get(template_type, "")
        if not template:
            return {"subject": f"Re: {template_type}", "body": "Please see the details below."}

        prompt = _fill_template(template, params)
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=512,
            )
            raw = resp.choices[0].message.content or "{}"
            raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw)
            return json.loads(raw)
        except Exception as e:
            logger.warning("CommsAgent draft_email failed: %s", e)
            return {
                "subject": f"[{template_type}] Action Required",
                "body": f"Please review: {json.dumps(params)}",
            }

    def _send_email(self, to: str, subject: str, body: str) -> dict:
        """Send via SMTP. Requires SMTP_HOST / SMTP_USER / SMTP_PASS / SMTP_FROM env vars."""
        smtp_host = os.environ.get("SMTP_HOST", "")
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")
        smtp_from = os.environ.get("SMTP_FROM", "sourceiq@yourdomain.com")

        if not all([smtp_host, smtp_user, smtp_pass, to]):
            logger.warning("SMTP not configured or missing recipient — email not sent")
            return {"status": "not_sent", "reason": "SMTP not configured"}

        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = smtp_from
            msg["To"] = to
            with smtplib.SMTP_SSL(smtp_host, 465) as server:
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            logger.info("Email sent to %s: %s", to, subject)
            return {"status": "sent", "to": to}
        except Exception as e:
            logger.error("SMTP send failed: %s", e)
            return {"status": "failed", "error": str(e)}

    def _log_comm(
        self,
        project_id: str = "",
        supplier_id: str = "",
        email_type: str = "",
        subject: str = "",
        body: str = "",
    ) -> dict:
        entry = {
            "project_id": project_id,
            "supplier_id": supplier_id,
            "type": email_type,
            "subject": subject,
            "sent_at": datetime.utcnow().isoformat(),
        }
        _comm_log.setdefault(project_id, []).append(entry)
        return entry

    def get_log(self, project_id: str) -> List[dict]:
        return _comm_log.get(project_id, [])

    def run(self, input: dict, context: Optional[Dict] = None) -> dict:
        email_type = input.get("type", "")
        params = {k: v for k, v in input.items() if k not in ("type", "auto_send")}

        drafted = self._draft_email(email_type, params)
        result: dict = {"drafted": drafted, "sent": False, "type": email_type}

        if input.get("auto_send", False):
            send_result = self._send_email(
                to=input.get("recipient_email", ""),
                subject=drafted.get("subject", ""),
                body=drafted.get("body", ""),
            )
            result["sent"] = send_result.get("status") == "sent"
            result["send_result"] = send_result

        self._log_comm(
            project_id=input.get("project_id", ""),
            supplier_id=input.get("supplier_id", ""),
            email_type=email_type,
            subject=drafted.get("subject", ""),
            body=drafted.get("body", ""),
        )

        return result
