"""Batch / scheduled reactive agent tasks.

Handles:
  - nightly_batch: Refresh all recommendation scores, remove disqualified,
    add new qualifiers, never resurface dismissed/acted-on
  - cert_expiry_scan: Scan for certs expiring within 30 days
  - score_refresh: Manual trigger for full score recalculation
"""

import logging
from datetime import date, timedelta, datetime, timezone
from typing import Any

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload, EventType
from app.database import SessionLocal
from app.models.technician import (
    Technician,
    TechnicianCertification,
    TechnicianDocument,
    DeployabilityStatus,
    CareerStage,
    CertStatus,
    ProficiencyLevel,
    VerificationStatus,
)
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.assignment import Assignment
from app.models.recommendation import Recommendation, PreferenceRule, RecommendationStatus
from app.services.scoring import rank_technicians_for_role, score_technician_for_role
from app.services.agent_llm import generate_staffing_explanation
from app.services.smart_merge import smart_merge_nightly_batch
from app.services.ws_broadcast import (
    publish_recommendation_list_refresh,
    publish_badge_count_update,
    publish_notification,
)

logger = logging.getLogger("deployable.workers.batch")


def _enum_val(v):
    return v.value if hasattr(v, "value") else str(v) if v else ""


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.batch.nightly_batch",
)
def nightly_batch(self, event_dict: dict[str, Any] = None) -> dict[str, Any]:
    """Nightly batch job that refreshes all recommendation scores via smart merge.

    Smart merge lifecycle:
      1. Re-score all eligible technicians per unfilled role
      2. Compare new evaluations against existing pending recommendations
      3. Update existing recs in-place (preserving context and score history)
      4. Supersede disqualified or dropped candidates with reasons
      5. Add new qualifying candidates that weren't previously recommended
      6. NEVER resurface dismissed or already-acted-on recommendations

    Uses the smart merge algorithm for deduplication-safe, context-preserving
    recommendation updates.
    """
    if event_dict is None:
        event_dict = EventPayload(
            event_type=EventType.NIGHTLY_BATCH_TRIGGERED,
            entity_type="system",
            entity_id="nightly",
            actor_id="system",
        ).to_dict()

    session = SessionLocal()
    try:
        # Use the smart merge algorithm for the entire nightly batch
        aggregate = smart_merge_nightly_batch(
            session=session,
            generate_explanation_fn=generate_staffing_explanation,
        )

        # Map to legacy stats format for backward compatibility
        stats = {
            "refreshed": aggregate["total_updated"],
            "superseded": aggregate["total_superseded"],
            "new_candidates_added": aggregate["total_added"],
            "roles_processed": aggregate["roles_processed"],
            "unchanged": aggregate["total_unchanged"],
            "terminal_skipped": aggregate["total_terminal_skipped"],
            "batch_id": aggregate["batch_id"],
        }

        logger.info(
            "Nightly batch (smart merge) complete: %d updated, %d superseded, "
            "%d added, %d unchanged for %d roles (batch: %s)",
            stats["refreshed"], stats["superseded"],
            stats["new_candidates_added"], stats["roles_processed"],
            aggregate["batch_id"],
        )

        # --- WebSocket broadcast: nightly batch complete ---
        pending_count = session.query(Recommendation).filter(
            Recommendation.status == RecommendationStatus.PENDING.value,
        ).count()

        publish_recommendation_list_refresh(
            summary={
                "action": "nightly_batch",
                "refreshed": stats["refreshed"],
                "superseded": stats["superseded"],
                "new_candidates": stats["new_candidates_added"],
                "roles_processed": stats["roles_processed"],
            },
            pending_count=pending_count,
        )
        publish_badge_count_update(
            badge_type="pending_recommendations",
            count=pending_count,
            role="ops",
        )
        if stats["new_candidates_added"] > 0 or stats["superseded"] > 0:
            publish_notification(
                notification_type="nightly_batch",
                title="Nightly Batch Complete",
                message=(
                    f"Refreshed {stats['refreshed']}, superseded {stats['superseded']}, "
                    f"added {stats['new_candidates_added']} new candidates"
                ),
                role="ops",
                severity="info",
                link="/agent-inbox",
            )

        return {"status": "completed", **stats}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.batch.cert_expiry_scan",
)
def cert_expiry_scan(self, event_dict: dict[str, Any] = None) -> dict[str, Any]:
    """Scan for certifications expiring within 30 days.

    Creates CERT_EXPIRING_SOON events for each one found.
    """
    if event_dict is None:
        event_dict = EventPayload(
            event_type=EventType.CERT_EXPIRY_SCAN_TRIGGERED,
            entity_type="system",
            entity_id="cert_scan",
            actor_id="system",
        ).to_dict()

    session = SessionLocal()
    try:
        today = date.today()
        threshold = today + timedelta(days=30)

        expiring_certs = session.query(TechnicianCertification).filter(
            TechnicianCertification.expiry_date != None,
            TechnicianCertification.expiry_date <= threshold,
            TechnicianCertification.expiry_date >= today,
            TechnicianCertification.status == CertStatus.ACTIVE,
        ).all()

        cascade_events = []
        alerts_created = 0

        for cert in expiring_certs:
            days_until = (cert.expiry_date - today).days

            if days_until <= 0:
                cert.status = CertStatus.EXPIRED
                cascade_events.append(
                    EventPayload(
                        event_type=EventType.CERT_EXPIRED,
                        entity_type="certification",
                        entity_id=str(cert.id),
                        actor_id="system",
                        data={
                            "technician_id": str(cert.technician_id),
                            "cert_name": cert.cert_name,
                            "days_until_expiry": days_until,
                        },
                    ).to_dict()
                )
            else:
                # Don't change status to "Expiring Soon" since CertStatus enum doesn't have it
                cascade_events.append(
                    EventPayload(
                        event_type=EventType.CERT_EXPIRING_SOON,
                        entity_type="certification",
                        entity_id=str(cert.id),
                        actor_id="system",
                        data={
                            "technician_id": str(cert.technician_id),
                            "cert_name": cert.cert_name,
                            "days_until_expiry": days_until,
                        },
                    ).to_dict()
                )

            alerts_created += 1

        session.commit()

        logger.info("Cert expiry scan: %d certs found expiring within 30 days", alerts_created)

        # --- WebSocket broadcast: cert expiry alerts ---
        if alerts_created > 0:
            publish_badge_count_update(
                badge_type="expiring_certs",
                count=alerts_created,
                role="ops",
            )
            publish_notification(
                notification_type="cert_expiry_scan",
                title="Certification Expiry Alert",
                message=f"{alerts_created} certifications expiring within 30 days",
                role="ops",
                severity="warning",
                link="/technicians?filter=expiring_certs",
            )

        return {
            "status": "completed",
            "certs_found": alerts_created,
            "cascade_events": cascade_events,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.batch.score_refresh",
)
def score_refresh(self, event_dict: dict[str, Any] = None) -> dict[str, Any]:
    """Manual trigger for full score recalculation across all pending recommendations."""
    if event_dict is None:
        event_dict = EventPayload(
            event_type=EventType.SCORE_REFRESH_TRIGGERED,
            entity_type="system",
            entity_id="manual_refresh",
            actor_id="system",
        ).to_dict()

    return nightly_batch(event_dict=event_dict)


# ---------------------------------------------------------------------------
# Nightly Readiness Re-evaluation
# ---------------------------------------------------------------------------

def _evaluate_technician_deployability(
    session,
    technician: Technician,
) -> DeployabilityStatus:
    """Deterministically compute the correct deployability status for a technician.

    Priority order (highest to lowest):
      1. Currently Assigned — has an active assignment
      2. Rolling Off Soon — active assignment ending within 30 days
      3. Missing Cert — any required certification is expired or missing
      4. Missing Docs — any required document is not verified
      5. In Training — career stage is In Training or Sourced/Screened
      6. Ready Now — all training complete, certs active, docs verified, no active assignment
      7. Inactive — explicit inactive flag (career_stage or manual lock)

    Note: deployability_locked technicians are skipped by the caller.
    """
    career_stage = technician.career_stage
    cs_val = career_stage.value if hasattr(career_stage, "value") else str(career_stage) if career_stage else ""

    # Check for Inactive — Sourced stage with no real progress is inactive-like
    if cs_val == CareerStage.SOURCED.value:
        return DeployabilityStatus.IN_TRAINING

    # Check active assignments
    today = date.today()
    active_assignments = (
        session.query(Assignment)
        .filter(
            Assignment.technician_id == technician.id,
            Assignment.status == "Active",
        )
        .all()
    )

    if active_assignments:
        # Check if any active assignment ends within 30 days (rolling off soon)
        rolling_off = False
        for assignment in active_assignments:
            if assignment.end_date:
                days_remaining = (assignment.end_date - today).days
                if 0 < days_remaining <= 30:
                    rolling_off = True
                    break

        if rolling_off:
            return DeployabilityStatus.ROLLING_OFF_SOON
        return DeployabilityStatus.CURRENTLY_ASSIGNED

    # Check career stage — still in training pipeline
    if cs_val in (
        CareerStage.SCREENED.value,
        CareerStage.IN_TRAINING.value,
    ):
        return DeployabilityStatus.IN_TRAINING

    # Check certifications — any expired or revoked?
    for cert in technician.certifications:
        cert_status = cert.status.value if hasattr(cert.status, "value") else str(cert.status) if cert.status else ""
        if cert_status in (CertStatus.EXPIRED.value, CertStatus.REVOKED.value):
            return DeployabilityStatus.MISSING_CERT
        # Check if cert is expiring (past expiry date)
        if cert.expiry_date and cert.expiry_date < today:
            return DeployabilityStatus.MISSING_CERT

    # Check documents — any not verified?
    if technician.documents:
        all_verified = all(
            (doc.verification_status.value if hasattr(doc.verification_status, "value")
             else str(doc.verification_status) if doc.verification_status else "")
            == VerificationStatus.VERIFIED.value
            for doc in technician.documents
        )
        if not all_verified:
            return DeployabilityStatus.MISSING_DOCS
    elif cs_val in (CareerStage.TRAINING_COMPLETED.value, CareerStage.AWAITING_ASSIGNMENT.value):
        # No documents at all for someone past training — flag as missing docs
        pass  # Allow to fall through to Ready Now if docs_verified flag is set

    # If docs_verified flag is explicitly False, mark missing docs
    if technician.docs_verified is False and cs_val in (
        CareerStage.TRAINING_COMPLETED.value,
        CareerStage.AWAITING_ASSIGNMENT.value,
        CareerStage.DEPLOYED.value,
    ):
        # Only flag if there are actually documents to verify
        if technician.documents:
            return DeployabilityStatus.MISSING_DOCS

    # If past training and all checks pass → Ready Now
    if cs_val in (
        CareerStage.TRAINING_COMPLETED.value,
        CareerStage.AWAITING_ASSIGNMENT.value,
        CareerStage.DEPLOYED.value,
    ):
        return DeployabilityStatus.READY_NOW

    # Default fallback
    return DeployabilityStatus.IN_TRAINING


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.batch.nightly_readiness_reeval",
)
def nightly_readiness_reeval(self, event_dict: dict[str, Any] = None) -> dict[str, Any]:
    """Nightly batch job that re-evaluates deployability status for ALL technicians.

    Queries every technician in the system and deterministically recomputes
    their deployability status based on current state:
      - Active assignments (Currently Assigned / Rolling Off Soon)
      - Certification validity (Missing Cert)
      - Document verification (Missing Docs)
      - Training stage (In Training)
      - All clear (Ready Now)

    Skips technicians with deployability_locked=True.
    Broadcasts WebSocket notification with summary when status changes occur.

    This task runs at 2:30 AM UTC, 30 minutes after the nightly score refresh,
    ensuring recommendations are up-to-date before readiness is re-evaluated.
    """
    if event_dict is None:
        event_dict = EventPayload(
            event_type=EventType.NIGHTLY_READINESS_TRIGGERED,
            entity_type="system",
            entity_id="nightly_readiness",
            actor_id="system",
        ).to_dict()

    session = SessionLocal()
    try:
        # Query all technicians
        technicians = session.query(Technician).all()

        stats = {
            "total_evaluated": 0,
            "skipped_locked": 0,
            "status_changed": 0,
            "unchanged": 0,
            "transitions": [],
        }

        cascade_events = []

        for technician in technicians:
            # Skip locked technicians
            if technician.deployability_locked:
                stats["skipped_locked"] += 1
                continue

            stats["total_evaluated"] += 1

            # Compute correct deployability status
            new_status = _evaluate_technician_deployability(session, technician)

            # Get current status value for comparison
            current_status_val = (
                technician.deployability_status.value
                if hasattr(technician.deployability_status, "value")
                else str(technician.deployability_status) if technician.deployability_status else ""
            )
            new_status_val = new_status.value

            if current_status_val != new_status_val:
                old_status = current_status_val
                technician.deployability_status = new_status
                technician.updated_at = datetime.now(timezone.utc)
                stats["status_changed"] += 1
                stats["transitions"].append({
                    "technician_id": str(technician.id),
                    "technician_name": technician.full_name,
                    "old_status": old_status,
                    "new_status": new_status_val,
                })

                # Cascade TECHNICIAN_STATUS_CHANGED for downstream processing
                cascade_events.append(
                    EventPayload(
                        event_type=EventType.TECHNICIAN_STATUS_CHANGED,
                        entity_type="technician",
                        entity_id=str(technician.id),
                        actor_id="system",
                        data={
                            "technician_id": str(technician.id),
                            "old_status": old_status,
                            "new_status": new_status_val,
                            "source": "nightly_readiness_reeval",
                        },
                    ).to_dict()
                )
            else:
                stats["unchanged"] += 1

        session.commit()

        # Log summary
        logger.info(
            "Nightly readiness re-eval complete: %d evaluated, %d changed, "
            "%d unchanged, %d locked/skipped",
            stats["total_evaluated"],
            stats["status_changed"],
            stats["unchanged"],
            stats["skipped_locked"],
        )

        if stats["transitions"]:
            logger.info(
                "Status transitions: %s",
                "; ".join(
                    f"{t['technician_name']}: {t['old_status']} → {t['new_status']}"
                    for t in stats["transitions"][:20]  # Log first 20 to avoid huge logs
                ),
            )

        # Broadcast WebSocket summary notification if any changes
        if stats["status_changed"] > 0:
            try:
                import json
                import os
                import redis as redis_lib

                redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
                r = redis_lib.Redis.from_url(redis_url, decode_responses=True)
                r.publish("deployable:ws_broadcast", json.dumps({
                    "topic": "dashboard",
                    "event": {
                        "event_type": "batch.readiness_reeval_complete",
                        "topic": "dashboard",
                        "data": {
                            "total_evaluated": stats["total_evaluated"],
                            "status_changed": stats["status_changed"],
                            "transitions": stats["transitions"][:10],
                            "message": (
                                f"Nightly readiness re-evaluation: {stats['status_changed']} "
                                f"technician(s) had status changes"
                            ),
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                }, default=str))
            except Exception:
                logger.warning("Failed to broadcast readiness reeval WS notification", exc_info=True)

        return {
            "status": "completed",
            "total_evaluated": stats["total_evaluated"],
            "status_changed": stats["status_changed"],
            "unchanged": stats["unchanged"],
            "skipped_locked": stats["skipped_locked"],
            "transitions": stats["transitions"],
            "cascade_events": cascade_events,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
