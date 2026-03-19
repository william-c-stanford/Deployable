"""Certification lifecycle reactive agent tasks.

Handles:
  - CERT_ADDED / CERT_RENEWED: Recalculate deployability status
  - CERT_EXPIRED / CERT_REVOKED: Flag technician, create alerts
  - CERT_EXPIRING_SOON: Create renewal reminder
"""

import logging
from datetime import date
from typing import Any

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload, EventType
from app.database import SessionLocal
from app.models.technician import (
    Technician,
    TechnicianCertification,
    DeployabilityStatus,
    CertStatus,
    VerificationStatus,
)
from app.models.recommendation import Recommendation, RecommendationStatus
from app.models.audit import SuggestedAction
from app.services.agent_llm import generate_cert_alert_explanation

logger = logging.getLogger("deployable.workers.certification")


def _enum_val(v):
    return v.value if hasattr(v, "value") else str(v) if v else ""


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.certification.recalc_deployability_for_cert",
)
def recalc_deployability_for_cert(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Recalculate technician's deployability after cert change."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        tech_id = payload.data.get("technician_id", payload.entity_id)
        technician = session.get(Technician, tech_id)
        if not technician:
            return {"status": "skipped", "reason": "Technician not found"}

        if technician.deployability_locked:
            return {"status": "skipped", "reason": "Deployability locked"}

        certs = session.query(TechnicianCertification).filter(
            TechnicianCertification.technician_id == technician.id
        ).all()

        has_expired = any(
            _enum_val(c.status) in (CertStatus.EXPIRED.value, CertStatus.REVOKED.value)
            for c in certs
        )
        old_status = _enum_val(technician.deployability_status)
        cascade_events = []

        if has_expired:
            if old_status not in (
                DeployabilityStatus.INACTIVE.value,
                DeployabilityStatus.MISSING_DOCS.value,
            ):
                technician.deployability_status = DeployabilityStatus.MISSING_CERT
                session.commit()
                cascade_events.append(
                    EventPayload(
                        event_type=EventType.TECHNICIAN_STATUS_CHANGED,
                        entity_type="technician",
                        entity_id=str(tech_id),
                        actor_id="system",
                        data={"old_status": old_status, "new_status": DeployabilityStatus.MISSING_CERT.value},
                    ).to_dict()
                )
        elif old_status == DeployabilityStatus.MISSING_CERT.value and not has_expired:
            docs_ok = all(
                _enum_val(d.verification_status) == VerificationStatus.VERIFIED.value
                for d in technician.documents
            ) if technician.documents else True

            cs = _enum_val(technician.career_stage)
            if docs_ok and cs in ("Training Completed", "Awaiting Assignment", "Deployed"):
                technician.deployability_status = DeployabilityStatus.READY_NOW
            else:
                technician.deployability_status = DeployabilityStatus.IN_TRAINING
            session.commit()
            cascade_events.append(
                EventPayload(
                    event_type=EventType.TECHNICIAN_STATUS_CHANGED,
                    entity_type="technician",
                    entity_id=str(tech_id),
                    actor_id="system",
                    data={"old_status": old_status, "new_status": _enum_val(technician.deployability_status)},
                ).to_dict()
            )

        return {
            "status": "recalculated",
            "technician_id": str(tech_id),
            "old_status": old_status,
            "new_status": _enum_val(technician.deployability_status),
            "has_expired_certs": has_expired,
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
    name="app.workers.tasks.certification.handle_cert_expiry",
)
def handle_cert_expiry(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Handle a certification expiry or revocation — create renewal recommendation."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        cert_id = payload.entity_id
        cert = session.get(TechnicianCertification, cert_id)
        if not cert:
            return {"status": "skipped", "reason": "Certification not found"}

        tech_id = cert.technician_id
        technician = session.get(Technician, tech_id)
        tech_name = technician.full_name if technician else "Unknown"

        explanation = generate_cert_alert_explanation(
            technician_name=tech_name,
            cert_name=cert.cert_name,
            expiry_date=str(cert.expiry_date) if cert.expiry_date else "N/A",
            days_until_expiry=0,
        )

        rec = Recommendation(
            recommendation_type="cert_renewal",
            target_entity_type="technician",
            target_entity_id=str(tech_id),
            technician_id=str(tech_id),
            scorecard={
                "cert_name": cert.cert_name,
                "cert_id": str(cert_id),
                "status": _enum_val(cert.status),
                "expiry_date": str(cert.expiry_date) if cert.expiry_date else None,
            },
            explanation=explanation,
            status=RecommendationStatus.PENDING.value,
            agent_name="certification_agent",
        )
        session.add(rec)

        action = SuggestedAction(
            target_role="ops",
            action_type="cert_expired",
            title=f"Cert Expired: {cert.cert_name}",
            description=f"{tech_name}'s {cert.cert_name} has expired. Schedule renewal to restore deployment eligibility.",
            link=f"/technicians/{tech_id}",
            priority=5,
        )
        session.add(action)
        session.commit()

        return {
            "status": "alert_created",
            "technician_id": str(tech_id),
            "cert_name": cert.cert_name,
            "recommendation_id": str(rec.id),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.certification.create_cert_renewal_alert",
)
def create_cert_renewal_alert(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Create a proactive renewal alert for an expiring-soon cert."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        cert_id = payload.entity_id
        cert = session.get(TechnicianCertification, cert_id)
        if not cert:
            return {"status": "skipped", "reason": "Certification not found"}

        tech_id = cert.technician_id
        technician = session.get(Technician, tech_id)
        tech_name = technician.full_name if technician else "Unknown"

        days_until = (cert.expiry_date - date.today()).days if cert.expiry_date else 999

        explanation = generate_cert_alert_explanation(
            technician_name=tech_name,
            cert_name=cert.cert_name,
            expiry_date=str(cert.expiry_date) if cert.expiry_date else "N/A",
            days_until_expiry=days_until,
        )

        rec = Recommendation(
            recommendation_type="cert_renewal",
            target_entity_type="technician",
            target_entity_id=str(tech_id),
            technician_id=str(tech_id),
            scorecard={
                "cert_name": cert.cert_name,
                "cert_id": str(cert_id),
                "days_until_expiry": days_until,
                "expiry_date": str(cert.expiry_date) if cert.expiry_date else None,
            },
            explanation=explanation,
            status=RecommendationStatus.PENDING.value,
            agent_name="certification_agent",
        )
        session.add(rec)

        action = SuggestedAction(
            target_role="ops",
            action_type="cert_expiring",
            title=f"Cert Expiring: {cert.cert_name} ({days_until}d)",
            description=f"{tech_name}'s {cert.cert_name} expires in {days_until} days. Schedule renewal.",
            link=f"/technicians/{tech_id}",
            priority=4 if days_until <= 14 else 3,
        )
        session.add(action)
        session.commit()

        return {
            "status": "alert_created",
            "technician_id": str(tech_id),
            "cert_name": cert.cert_name,
            "days_until_expiry": days_until,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
