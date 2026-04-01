"""
deadline_agent.py

Handles FM-5.1 (deadline tracking + automated reminders).

Background scheduler that checks all active projects every hour
and sends deadline reminders at 7 days, 3 days, and 1 day before submission.

To activate: call start_deadline_scheduler() from main.py startup event.
"""
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_scheduler = None


def _check_deadlines() -> None:
    """Hourly job: scan projects and send due reminders."""
    try:
        from app.services.project_store import get_all_active_projects
        from app.agents.comms_agent import CommsAgent

        projects = get_all_active_projects() if callable(
            getattr(__import__('app.services.project_store', fromlist=['get_all_active_projects']),
                     'get_all_active_projects', None)
        ) else []

        comms = CommsAgent()
        reminded = 0

        for project in projects:
            deadline_str = project.get("submission_deadline")
            if not deadline_str:
                continue
            try:
                deadline = datetime.fromisoformat(str(deadline_str))
            except ValueError:
                continue

            days_left = (deadline - datetime.utcnow()).days
            if days_left not in (7, 3, 1):
                continue

            for supplier in project.get("invited_suppliers", []):
                if supplier.get("status") == "submitted":
                    continue
                comms.run(
                    {
                        "type": "deadline_reminder",
                        "project_name": project.get("name", project.get("project_id", "")),
                        "supplier_name": supplier.get("name", ""),
                        "recipient_email": supplier.get("email", ""),
                        "deadline": str(deadline.date()),
                        "days_remaining": days_left,
                        "response_status": supplier.get("status", "not_submitted"),
                        "auto_send": False,  # draft only — change to True when SMTP configured
                    }
                )
                reminded += 1

        if reminded:
            logger.info("DeadlineAgent: drafted %d reminder(s)", reminded)

    except Exception as e:
        logger.error("DeadlineAgent check_deadlines error: %s", e)


def start_deadline_scheduler() -> None:
    """
    Start the APScheduler background job.
    Call this from main.py startup event.

    Requires: pip install apscheduler
    """
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        _scheduler = BackgroundScheduler()
        _scheduler.add_job(_check_deadlines, "interval", hours=1, id="deadline_check")
        _scheduler.start()
        logger.info("DeadlineAgent scheduler started — checking every 1 hour")
    except ImportError:
        logger.warning(
            "APScheduler not installed — deadline reminders disabled. "
            "Run: pip install apscheduler"
        )
    except Exception as e:
        logger.error("Failed to start DeadlineAgent scheduler: %s", e)


def stop_deadline_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("DeadlineAgent scheduler stopped")
