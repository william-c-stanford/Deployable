"""Notification badge counts and real-time notification endpoints.

Provides:
- GET /api/notifications/badge-counts — aggregated badge counts per role
- POST /api/notifications/broadcast — manual broadcast trigger (ops only)
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.recommendation import Recommendation, RecommendationStatus
from app.models.assignment_confirmation import AssignmentConfirmation
from app.models.timesheet import Timesheet, TimesheetStatus
from app.models.technician import TechnicianCertification, CertStatus
from app.services.ws_broadcast import publish_badge_count_update, publish_notification

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/badge-counts")
def get_badge_counts(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return aggregated badge counts scoped to the current user's role.

    Ops sees: pending_recommendations, pending_confirmations, expiring_certs,
              pending_timesheets, escalations
    Partners see: pending_confirmations, pending_timesheets
    Technicians see: pending_actions (recommendations about them)
    """
    counts = {}

    if current_user.role == "ops":
        # Pending recommendations count
        pending_recs = db.query(func.count(Recommendation.id)).filter(
            Recommendation.status == RecommendationStatus.PENDING.value,
        ).scalar() or 0
        counts["pending_recommendations"] = pending_recs

        # Pending confirmations count
        try:
            pending_confs = db.query(func.count(AssignmentConfirmation.id)).filter(
                AssignmentConfirmation.status == "Pending",
            ).scalar() or 0
            counts["pending_confirmations"] = pending_confs
        except Exception:
            counts["pending_confirmations"] = 0

        # Pending timesheets (submitted, awaiting ops review)
        try:
            pending_timesheets = db.query(func.count(Timesheet.id)).filter(
                Timesheet.status == TimesheetStatus.SUBMITTED.value,
            ).scalar() or 0
            counts["pending_timesheets"] = pending_timesheets
        except Exception:
            counts["pending_timesheets"] = 0

        # Expiring certs (within 30 days)
        try:
            from datetime import date, timedelta
            threshold = date.today() + timedelta(days=30)
            expiring_certs = db.query(func.count(TechnicianCertification.id)).filter(
                TechnicianCertification.expiry_date != None,
                TechnicianCertification.expiry_date <= threshold,
                TechnicianCertification.expiry_date >= date.today(),
                TechnicianCertification.status == CertStatus.ACTIVE,
            ).scalar() or 0
            counts["expiring_certs"] = expiring_certs
        except Exception:
            counts["expiring_certs"] = 0

        # Total badge count (sum of all actionable items)
        counts["total"] = sum(counts.values())

    elif current_user.role == "partner":
        # Partners: pending confirmations for their assignments
        try:
            pending_confs = db.query(func.count(AssignmentConfirmation.id)).filter(
                AssignmentConfirmation.status == "Pending",
            ).scalar() or 0
            counts["pending_confirmations"] = pending_confs
        except Exception:
            counts["pending_confirmations"] = 0

        try:
            pending_timesheets = db.query(func.count(Timesheet.id)).filter(
                Timesheet.status == TimesheetStatus.SUBMITTED.value,
            ).scalar() or 0
            counts["pending_timesheets"] = pending_timesheets
        except Exception:
            counts["pending_timesheets"] = 0

        counts["total"] = sum(counts.values())

    elif current_user.role == "technician":
        # Technicians: recommendations about them that are pending
        try:
            pending_actions = db.query(func.count(Recommendation.id)).filter(
                Recommendation.technician_id == current_user.user_id,
                Recommendation.status == RecommendationStatus.PENDING.value,
            ).scalar() or 0
            counts["pending_actions"] = pending_actions
        except Exception:
            counts["pending_actions"] = 0

        counts["total"] = sum(counts.values())

    return {"role": current_user.role, "counts": counts}


@router.post("/broadcast")
async def manual_broadcast(
    badge_type: str = Query(..., description="Badge type to broadcast"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Manually trigger a badge count broadcast (ops only, for testing/debugging)."""
    # Re-compute the count for the requested badge type
    count = 0
    if badge_type == "pending_recommendations":
        count = db.query(func.count(Recommendation.id)).filter(
            Recommendation.status == RecommendationStatus.PENDING.value,
        ).scalar() or 0
    elif badge_type == "pending_confirmations":
        try:
            count = db.query(func.count(AssignmentConfirmation.id)).filter(
                AssignmentConfirmation.status == "Pending",
            ).scalar() or 0
        except Exception:
            pass
    elif badge_type == "pending_timesheets":
        try:
            count = db.query(func.count(Timesheet.id)).filter(
                Timesheet.status == TimesheetStatus.SUBMITTED.value,
            ).scalar() or 0
        except Exception:
            pass

    publish_badge_count_update(
        badge_type=badge_type,
        count=count,
        role="ops",
    )

    return {"badge_type": badge_type, "count": count, "broadcast": True}
