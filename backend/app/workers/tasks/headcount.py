"""Headcount request post-approval reactive agent task.

Handles:
  - HEADCOUNT_APPROVED: Convert approved PendingHeadcountRequest into actual
    ProjectRole slot records, emit WebSocket notifications to the requesting
    partner, and optionally trigger staffing agent to find candidates.

This task runs AFTER a human ops user approves a headcount request (human
approval gate). It is NOT an autonomous mutation — it executes the
consequences of an explicit human decision.
"""

import logging
from typing import Any

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload
from app.database import SessionLocal
from app.services.headcount_approval import execute_headcount_approval

logger = logging.getLogger("deployable.workers.headcount")


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.headcount.process_approved_headcount",
)
def process_approved_headcount(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Convert an approved PendingHeadcountRequest into ProjectRole slot records.

    Delegates to app.services.headcount_approval.execute_headcount_approval
    for the core business logic.
    """
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        return execute_headcount_approval(session, payload)
    except Exception:
        session.rollback()
        logger.exception(
            "Error processing approved headcount request %s",
            payload.entity_id,
        )
        raise
    finally:
        session.close()
