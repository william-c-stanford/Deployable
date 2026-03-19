"""Base reactive agent task class for Deployable Celery workers.

All reactive agent tasks inherit from ReactiveAgentTask, which provides:
- Automatic DB session management (sync, using app.database)
- Structured event payload deserialization
- Standardized logging and error handling
- Cascading event dispatch for domino-effect chains
- Audit trail creation for every processed event
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from celery import Task

from app.database import SessionLocal
from app.workers.events import EventPayload, EventType, EventCategory, EVENT_CATEGORY_MAP

logger = logging.getLogger("deployable.workers")


class ReactiveAgentTask(Task):
    """Base class for all event-driven reactive agent tasks.

    Provides two usage patterns:

    1. **Function-style tasks** (preferred): Decorate a function with
       ``@celery_app.task(bind=True, base=ReactiveAgentTask)``.
       Use ``self._get_session()`` for DB access and call
       ``self._after_task(result, event_dict)`` at the end for
       audit logging and cascade dispatch.

    2. **Class-style tasks**: Subclass ReactiveAgentTask, override
       ``handle_event(session, payload)`` — the ``run()`` method
       manages the session lifecycle automatically.
    """

    abstract = True

    # Subclasses should declare which event types they handle
    handles_events: list[EventType] = []

    # Retry configuration defaults
    autoretry_for = (Exception,)
    retry_backoff = True
    retry_backoff_max = 300  # 5 minutes
    max_retries = 3
    retry_jitter = True

    # Whether this task creates audit log entries
    creates_audit_trail = True

    # ── Session helper ────────────────────────────────────────────────

    def _get_session(self):
        """Return a new sync SQLAlchemy session.  Caller must close it."""
        return SessionLocal()

    # ── Post-task hook (for function-style tasks) ─────────────────────

    def after_return(self, status, retval, task_id, args, kwargs, einfo):
        """Celery hook called after task returns.  Handles audit + cascade."""
        if status != "SUCCESS" or not isinstance(retval, dict):
            return

        event_dict = kwargs.get("event_dict") if kwargs else None
        if not event_dict:
            # Positional arg fallback
            event_dict = args[0] if args else None
        if not event_dict or not isinstance(event_dict, dict):
            return

        try:
            payload = EventPayload.from_dict(event_dict)
        except Exception:
            return

        # Audit log
        if self.creates_audit_trail:
            session = SessionLocal()
            try:
                self._write_audit_log(session, payload, retval)
                session.commit()
            except Exception:
                session.rollback()
                logger.warning("Audit log write failed for %s", payload.event_type.value)
            finally:
                session.close()

        # Cascade events
        cascading = retval.get("cascade_events", [])
        for cascade_event_dict in cascading:
            self._dispatch_cascade(cascade_event_dict, payload.correlation_id)

    # ── Class-style entry point ───────────────────────────────────────

    def run(self, event_dict: dict[str, Any]) -> dict[str, Any]:
        """Entry point for class-style tasks.  Override handle_event()."""
        payload = EventPayload.from_dict(event_dict)

        logger.info(
            "Processing event %s for %s/%s [correlation=%s]",
            payload.event_type.value,
            payload.entity_type,
            payload.entity_id,
            payload.correlation_id,
        )

        session = SessionLocal()
        try:
            result = self.handle_event(session, payload)
            session.commit()
            return result if isinstance(result, dict) else {"status": "ok"}
        except Exception as exc:
            session.rollback()
            logger.exception(
                "Error processing event %s for %s/%s: %s",
                payload.event_type.value,
                payload.entity_type,
                payload.entity_id,
                str(exc),
            )
            raise
        finally:
            session.close()

    def handle_event(self, session: Any, payload: EventPayload) -> dict[str, Any]:
        """Process the event.  Override in class-style subclasses.

        Args:
            session: SQLAlchemy session (sync).
            payload: Deserialized event payload.

        Returns:
            Dict with result data.  May include ``cascade_events`` key
            containing a list of EventPayload dicts to dispatch.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement handle_event()"
        )

    def _write_audit_log(
        self, session: Any, payload: EventPayload, result: Optional[dict]
    ) -> None:
        """Write an audit log entry for the processed event."""
        from app.models.event_log import EventLog

        try:
            log_entry = EventLog(
                event_type=payload.event_type.value,
                entity_type=payload.entity_type,
                entity_id=payload.entity_id,
                actor_id=payload.actor_id,
                correlation_id=payload.correlation_id,
                task_name=self.name or self.__class__.__name__,
                status="completed",
                result_summary=_truncate(str(result), 500) if result else None,
            )
            session.add(log_entry)
        except Exception:
            logger.warning("Failed to write audit log for event %s", payload.event_type.value)

    def _dispatch_cascade(
        self, cascade_event_dict: dict[str, Any], parent_correlation_id: str
    ) -> None:
        """Dispatch a cascading event through the event dispatcher."""
        from app.workers.dispatcher import dispatch_event

        # Preserve correlation chain
        if "correlation_id" not in cascade_event_dict:
            cascade_event_dict["correlation_id"] = parent_correlation_id

        cascade_payload = EventPayload.from_dict(cascade_event_dict)
        dispatch_event(cascade_payload)


def _truncate(s: str, max_len: int) -> str:
    return s[:max_len] + "..." if len(s) > max_len else s
