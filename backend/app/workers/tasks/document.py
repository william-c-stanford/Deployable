"""Document verification reactive agent tasks.

Handles:
  - DOC_UPLOADED / DOC_VERIFIED: Check document completeness
  - DOC_REJECTED / DOC_EXPIRED: Create follow-up actions
  - ALL_DOCS_VERIFIED: Update deployability to remove Missing Docs flag
"""

import logging
from typing import Any

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload, EventType
from app.database import SessionLocal
from app.models.technician import (
    Technician,
    TechnicianDocument,
    DeployabilityStatus,
    VerificationStatus,
    CertStatus,
)
from app.models.audit import SuggestedAction

logger = logging.getLogger("deployable.workers.document")

REQUIRED_DOC_TYPES = {"Background Check", "Drug Test", "W-4", "I-9"}


def _enum_val(v):
    return v.value if hasattr(v, "value") else str(v) if v else ""


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.document.check_doc_completeness",
)
def check_doc_completeness(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Check whether all required documents are verified for a technician."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        tech_id = payload.data.get("technician_id", payload.entity_id)
        technician = session.get(Technician, tech_id)
        if not technician:
            return {"status": "skipped", "reason": "Technician not found"}

        docs = session.query(TechnicianDocument).filter(
            TechnicianDocument.technician_id == technician.id
        ).all()
        verified_types = {
            d.doc_type for d in docs
            if _enum_val(d.verification_status) == VerificationStatus.VERIFIED.value
        }
        missing = REQUIRED_DOC_TYPES - verified_types

        cascade_events = []
        if not missing:
            cascade_events.append(
                EventPayload(
                    event_type=EventType.ALL_DOCS_VERIFIED,
                    entity_type="technician",
                    entity_id=str(tech_id),
                    actor_id="system",
                    data={"verified_types": list(verified_types)},
                ).to_dict()
            )

        return {
            "status": "checked",
            "technician_id": str(tech_id),
            "verified": list(verified_types),
            "missing": list(missing),
            "all_complete": len(missing) == 0,
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
    name="app.workers.tasks.document.update_deployability_docs_complete",
)
def update_deployability_docs_complete(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Update deployability when all documents are verified."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        tech_id = payload.entity_id
        technician = session.get(Technician, tech_id)
        if not technician:
            return {"status": "skipped", "reason": "Technician not found"}

        if technician.deployability_locked:
            return {"status": "skipped", "reason": "Deployability locked"}

        old_status = _enum_val(technician.deployability_status)

        if old_status == DeployabilityStatus.MISSING_DOCS.value:
            has_expired_certs = any(
                _enum_val(c.status) in (CertStatus.EXPIRED.value, CertStatus.REVOKED.value)
                for c in technician.certifications
            )
            cs = _enum_val(technician.career_stage)
            if has_expired_certs:
                technician.deployability_status = DeployabilityStatus.MISSING_CERT
            elif cs in ("Training Completed", "Awaiting Assignment", "Deployed"):
                technician.deployability_status = DeployabilityStatus.READY_NOW
            else:
                technician.deployability_status = DeployabilityStatus.IN_TRAINING

            session.commit()
            return {
                "status": "updated",
                "technician_id": str(tech_id),
                "old_status": old_status,
                "new_status": _enum_val(technician.deployability_status),
                "cascade_events": [
                    EventPayload(
                        event_type=EventType.TECHNICIAN_STATUS_CHANGED,
                        entity_type="technician",
                        entity_id=str(tech_id),
                        actor_id="system",
                        data={"old_status": old_status, "new_status": _enum_val(technician.deployability_status)},
                    ).to_dict()
                ],
            }

        return {"status": "no_change", "technician_id": str(tech_id), "current_status": old_status}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.document.handle_doc_rejection",
)
def handle_doc_rejection(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Handle a document rejection — create action for ops/technician."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        doc_id = payload.entity_id
        doc = session.get(TechnicianDocument, doc_id)
        if not doc:
            return {"status": "skipped", "reason": "Document not found"}

        tech_id = doc.technician_id
        technician = session.get(Technician, tech_id)
        tech_name = technician.full_name if technician else "Unknown"

        ops_action = SuggestedAction(
            target_role="ops",
            action_type="doc_rejected",
            title=f"Doc Rejected: {doc.doc_type}",
            description=f"{tech_name}'s {doc.doc_type} was rejected. Follow up for resubmission.",
            link=f"/technicians/{tech_id}",
            priority=4,
        )
        tech_action = SuggestedAction(
            target_role="technician",
            target_user_id=str(tech_id),
            action_type="doc_resubmit",
            title=f"Resubmit: {doc.doc_type}",
            description=f"Your {doc.doc_type} needs to be resubmitted. Contact your coordinator.",
            link="/my-profile/documents",
            priority=5,
        )
        session.add_all([ops_action, tech_action])

        if technician and not technician.deployability_locked:
            ds = _enum_val(technician.deployability_status)
            if ds == DeployabilityStatus.READY_NOW.value:
                technician.deployability_status = DeployabilityStatus.MISSING_DOCS

        session.commit()
        return {"status": "handled", "technician_id": str(tech_id), "doc_type": doc.doc_type}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.document.handle_doc_expiry",
)
def handle_doc_expiry(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Handle a document expiration — flag and create renewal action."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        doc_id = payload.entity_id
        doc = session.get(TechnicianDocument, doc_id)
        if not doc:
            return {"status": "skipped", "reason": "Document not found"}

        tech_id = doc.technician_id
        technician = session.get(Technician, tech_id)
        tech_name = technician.full_name if technician else "Unknown"

        action = SuggestedAction(
            target_role="ops",
            action_type="doc_expired",
            title=f"Doc Expired: {doc.doc_type}",
            description=f"{tech_name}'s {doc.doc_type} has expired and needs renewal.",
            link=f"/technicians/{tech_id}",
            priority=4,
        )
        session.add(action)

        if technician and not technician.deployability_locked:
            ds = _enum_val(technician.deployability_status)
            if ds not in (
                DeployabilityStatus.INACTIVE.value,
                DeployabilityStatus.MISSING_CERT.value,
            ):
                technician.deployability_status = DeployabilityStatus.MISSING_DOCS

        session.commit()
        return {"status": "handled", "technician_id": str(tech_id), "doc_type": doc.doc_type}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
