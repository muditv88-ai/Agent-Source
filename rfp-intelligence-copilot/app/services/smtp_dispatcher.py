"""
smtp_dispatcher.py  (was: communication_engine.py)

Low-level SMTP dispatcher. This is the ONLY file that touches the network
for email. Higher-level drafting + orchestration lives in agents/comms_agent.py.

All six procurement email types have a plain-text fallback template so the
app can always generate a body even when the LLM is unavailable.

Required environment variables:
    SMTP_HOST   — e.g. smtp.sendgrid.net
    SMTP_PORT   — default 465 (SSL) or 587 (STARTTLS)
    SMTP_USER   — SMTP username / API key name
    SMTP_PASS   — SMTP password / API key
    SMTP_FROM   — sender address shown on emails
"""
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Plain-text fallback templates (used when LLM drafting is unavailable)
# ---------------------------------------------------------------------------

FALLBACK_TEMPLATES = {
    "rfp_invite": (
        "Subject: Invitation to Respond — {project_name}\n\n"
        "Dear {supplier_name},\n\n"
        "You are invited to submit a response to our RFP for {project_name}.\n"
        "Submission deadline: {deadline}\n"
        "Portal: {portal_link}\n\n"
        "Regards,\nProcurement Team"
    ),
    "deadline_reminder": (
        "Subject: Reminder — RFP Deadline in {days_remaining} Days\n\n"
        "Dear {supplier_name},\n\n"
        "This is a reminder that your response to {project_name} is due on {deadline}.\n"
        "Current status: {response_status}\n\n"
        "Please submit at your earliest convenience.\n\nRegards,\nProcurement Team"
    ),
    "clarification_request": (
        "Subject: Clarification Required — {project_id}\n\n"
        "Dear {supplier_name},\n\n"
        "We require clarification on the following items in your submission:\n"
        "{missing_questions}\n\n"
        "Please respond within 2 business days.\n\nRegards,\nProcurement Team"
    ),
    "award_notification": (
        "Subject: Award Notification — {project_name}\n\n"
        "Dear {supplier_name},\n\n"
        "We are pleased to inform you that you have been awarded {project_name}.\n"
        "Award value: {award_value} | Items: {awarded_items}\n"
        "Next steps: {next_steps}\n\nRegards,\nProcurement Team"
    ),
    "regret_notification": (
        "Subject: Outcome of RFP — {project_name}\n\n"
        "Dear {supplier_name},\n\n"
        "Thank you for your response to {project_name}. After careful evaluation, "
        "we will not be proceeding with your submission on this occasion.\n"
        "Reason: {reason}\n\n"
        "We appreciate your participation and hope to work together in the future.\n\nRegards,\nProcurement Team"
    ),
    "onboarding_invite": (
        "Subject: Supplier Onboarding Invitation\n\n"
        "Dear {supplier_name},\n\n"
        "Please complete your supplier onboarding at: {portal_link}\n"
        "Documents required: company registration, tax ID, bank details, "
        "ISO/quality certifications, insurance certificate.\n\nRegards,\nProcurement Team"
    ),
    "missing_docs_request": (
        "Subject: Missing Onboarding Documents — Action Required\n\n"
        "Dear {supplier_name},\n\n"
        "We are missing the following documents for supplier {supplier_id}:\n"
        "{missing_items}\n\n"
        "Please upload them to the portal at your earliest convenience.\n\nRegards,\nProcurement Team"
    ),
}


class SafeDict(dict):
    """dict subclass that returns empty string for missing keys (safe .format_map)."""
    def __missing__(self, key: str) -> str:
        return ""


def build_fallback(email_type: str, params: dict) -> dict:
    """
    Build a plain-text email dict using the fallback template for email_type.
    Returns {"subject": str, "body": str}.
    """
    template = FALLBACK_TEMPLATES.get(email_type, "Subject: Notification\n\nPlease review the attached information.")
    filled = template.format_map(SafeDict(params))
    parts = filled.split("\n\n", 1)
    subject = parts[0].replace("Subject: ", "").strip()
    body = parts[1].strip() if len(parts) > 1 else filled
    return {"subject": subject, "body": body}


def send_email(
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
) -> dict:
    """
    Send a single email via SMTP.

    - Uses SSL on port 465 by default; switches to STARTTLS if SMTP_PORT=587.
    - If SMTP is not configured, logs a warning and returns status='not_sent'
      (so the app stays functional in local/test environments).

    Returns:
        {"status": "sent" | "not_sent" | "failed", ...}
    """
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    smtp_from = os.environ.get("SMTP_FROM", "sourceiq-noreply@yourdomain.com")

    if not all([smtp_host, smtp_user, smtp_pass, to]):
        logger.warning(
            "SMTP not fully configured (missing: %s) — email not sent to %s",
            ", ".join(k for k, v in {"SMTP_HOST": smtp_host, "SMTP_USER": smtp_user,
                                      "SMTP_PASS": smtp_pass, "recipient": to}.items() if not v),
            to,
        )
        return {"status": "not_sent", "reason": "SMTP not configured", "to": to}

    try:
        if html_body:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(html_body, "html"))
        else:
            msg = MIMEText(body, "plain")

        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = to

        if smtp_port == 587:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)

        logger.info("Email sent → %s | subject: %s", to, subject)
        return {"status": "sent", "to": to, "subject": subject}

    except smtplib.SMTPAuthenticationError as e:
        logger.error("SMTP auth failed: %s", e)
        return {"status": "failed", "error": "authentication_failed", "detail": str(e)}
    except smtplib.SMTPRecipientsRefused as e:
        logger.error("SMTP recipient refused: %s", e)
        return {"status": "failed", "error": "recipient_refused", "detail": str(e)}
    except Exception as e:
        logger.error("SMTP send failed: %s", e)
        return {"status": "failed", "error": "smtp_error", "detail": str(e)}


def draft_clarification_email(supplier_id: str, questions: list) -> dict:
    """
    Backward-compatible shim preserved from the original communication_engine.py.
    New code should call build_fallback('clarification_request', {...}) directly.
    """
    return build_fallback(
        "clarification_request",
        {
            "supplier_id": supplier_id,
            "supplier_name": f"Supplier {supplier_id}",
            "project_id": "",
            "missing_questions": "\n".join(f"- {q}" for q in questions),
        },
    )
