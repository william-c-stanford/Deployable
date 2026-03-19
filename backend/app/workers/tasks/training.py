"""Training & skill progression reactive agent tasks.

Handles:
  - TRAINING_HOURS_LOGGED: Check if technician hit proficiency thresholds
  - TRAINING_THRESHOLD_MET: Advance proficiency level (deterministic)
  - TRAINING_COMPLETED: Update career stage
  - TECHNICIAN_CREATED: Initialize training plan
  - TIMESHEET_SUBMITTED/APPROVED: Accumulate hours and re-check proficiency

These are the ONLY autonomous state mutations allowed — deterministic
training advancement based on hours thresholds with optional per-skill
certification gates.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import redis

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload, EventType
from app.database import SessionLocal
from app.models.technician import (
    Technician,
    TechnicianSkill,
    CareerStage,
    ProficiencyLevel,
)
from app.models.audit import SuggestedAction
from app.services.advancement import (
    evaluate_technician_advancement,
    evaluate_skill_advancement,
)

logger = logging.getLogger("deployable.workers.training")

# Redis pub/sub channel for WebSocket broadcast relay
WS_BROADCAST_CHANNEL = "deployable:ws_broadcast"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Deterministic proficiency thresholds (hours) — defaults
PROFICIENCY_THRESHOLDS = {
    ProficiencyLevel.INTERMEDIATE.value: 100,
    ProficiencyLevel.ADVANCED.value: 300,
}


def _enum_val(v):
    """Safely extract .value from an enum, or return str."""
    return v.value if hasattr(v, "value") else str(v) if v else ""


def _broadcast_ws_notification(topic: str, event: dict[str, Any]) -> None:
    """Push a WebSocket notification via Redis pub/sub.

    Celery workers run in a sync context without access to the FastAPI
    WebSocket ConnectionManager (async, lives in the web process).
    We publish to a Redis channel that the web process subscribes to
    and relays to connected WebSocket clients.
    """
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        message = json.dumps({"topic": topic, "event": event}, default=str)
        r.publish(WS_BROADCAST_CHANNEL, message)
        logger.info(
            "Published WS notification to topic '%s': %s",
            topic,
            event.get("event_type"),
        )
    except Exception:
        logger.warning("Failed to publish WS notification", exc_info=True)


# ---------------------------------------------------------------------------
# Task: Process approved timesheet → accumulate hours → check advancement
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.training.process_approved_timesheet",
)
def process_approved_timesheet(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Process an approved timesheet by accumulating hours on technician skills.

    This task:
    1. Finds the technician and the skill referenced in the timesheet
    2. Adds the approved hours to TechnicianSkill.training_hours_accumulated
    3. Updates Technician.total_approved_hours
    4. Cascades a TRAINING_HOURS_LOGGED event to trigger proficiency checks

    Triggered by: TIMESHEET_APPROVED events.
    """
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        tech_id = payload.data.get("technician_id")
        hours = payload.data.get("hours", 0)
        skill_name = payload.data.get("skill_name")

        if not tech_id:
            return {"status": "skipped", "reason": "No technician_id in event data"}

        technician = session.get(Technician, tech_id)
        if not technician:
            return {"status": "skipped", "reason": f"Technician {tech_id} not found"}

        # Update total approved hours on the technician
        technician.total_approved_hours = (technician.total_approved_hours or 0) + hours

        # Accumulate hours on the specific skill or distribute evenly
        updated_skills = []
        if skill_name:
            tech_skill = (
                session.query(TechnicianSkill)
                .filter(
                    TechnicianSkill.technician_id == tech_id,
                    TechnicianSkill.skill_name.ilike(skill_name),
                )
                .first()
            )
            if tech_skill:
                old_hours = tech_skill.training_hours_accumulated or 0.0
                tech_skill.training_hours_accumulated = old_hours + hours
                updated_skills.append({
                    "skill_name": tech_skill.skill_name,
                    "old_hours": old_hours,
                    "new_hours": tech_skill.training_hours_accumulated,
                    "skill_id": str(tech_skill.id),
                })
            else:
                logger.warning(
                    "Skill '%s' not found for technician %s — hours not attributed",
                    skill_name,
                    tech_id,
                )
        else:
            # No specific skill — distribute hours evenly across all skills
            if technician.skills:
                per_skill = hours / len(technician.skills)
                for ts in technician.skills:
                    old_hours = ts.training_hours_accumulated or 0.0
                    ts.training_hours_accumulated = old_hours + per_skill
                    updated_skills.append({
                        "skill_name": ts.skill_name,
                        "old_hours": old_hours,
                        "new_hours": ts.training_hours_accumulated,
                        "skill_id": str(ts.id),
                    })

        session.commit()

        # Cascade: TRAINING_HOURS_LOGGED to trigger proficiency advancement checks
        cascade_events = []
        if updated_skills:
            cascade_events.append(
                EventPayload(
                    event_type=EventType.TRAINING_HOURS_LOGGED,
                    entity_type="technician",
                    entity_id=str(tech_id),
                    actor_id="system",
                    data={
                        "technician_id": str(tech_id),
                        "hours_added": hours,
                        "updated_skills": updated_skills,
                    },
                ).to_dict()
            )

        # Broadcast WS notification about hours being approved
        _broadcast_ws_notification("technicians", {
            "event_type": "training.hours_approved",
            "topic": "technicians",
            "technician_id": str(tech_id),
            "technician_name": technician.full_name,
            "data": {
                "hours_approved": hours,
                "skill_name": skill_name,
                "total_approved_hours": technician.total_approved_hours,
                "skills_updated": len(updated_skills),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return {
            "status": "ok",
            "technician_id": str(tech_id),
            "hours_accumulated": hours,
            "skills_updated": updated_skills,
            "cascade_events": cascade_events,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Task: Check proficiency advancement
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.training.check_proficiency_advancement",
)
def check_proficiency_advancement(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Check if training hours trigger a proficiency level advancement.

    Uses the AdvancementEvaluationService to check both hours thresholds
    AND optional per-skill certification gates before allowing advancement.
    This is a DETERMINISTIC check — no LLM involved.
    """
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        tech_id = payload.data.get("technician_id", payload.entity_id)
        technician = session.get(Technician, tech_id)
        if not technician:
            return {"status": "skipped", "reason": f"Technician {tech_id} not found"}

        # Use the advancement evaluation service
        evaluation = evaluate_technician_advancement(session, technician)

        cascade_events = []
        blocked_skills = []

        for result in evaluation.skill_results:
            if result.should_advance:
                cascade_events.append(
                    EventPayload(
                        event_type=EventType.TRAINING_THRESHOLD_MET,
                        entity_type="technician_skill",
                        entity_id=result.technician_skill_id,
                        actor_id="system",
                        data={
                            "technician_id": str(tech_id),
                            "skill_name": result.skill_name,
                            "current_level": result.current_level,
                            "new_level": result.target_level,
                            "hours": result.hours_accumulated,
                        },
                    ).to_dict()
                )
            elif result.hours_met and result.cert_gate and not result.cert_gate.is_satisfied:
                # Hours met but blocked by cert gate — create alert for ops
                blocked_skills.append({
                    "skill_name": result.skill_name,
                    "target_level": result.target_level,
                    "blocked_reason": result.blocked_reason,
                    "required_cert": result.cert_gate.required_cert,
                })

        # Create suggested actions for cert-blocked advancements
        for blocked in blocked_skills:
            action = SuggestedAction(
                target_role="ops",
                action_type="cert_gate_blocked",
                title=f"Cert Gate: {blocked['skill_name']} → {blocked['target_level']}",
                description=(
                    f"{technician.full_name} has enough hours for "
                    f"{blocked['target_level']} in {blocked['skill_name']} but is "
                    f"blocked: {blocked['blocked_reason']}"
                ),
                link=f"/technicians/{tech_id}",
                priority=2,
            )
            session.add(action)

        session.commit()
        return {
            "status": "ok",
            "technician_id": str(tech_id),
            "advancements_detected": len(cascade_events),
            "blocked_by_cert_gate": len(blocked_skills),
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
    name="app.workers.tasks.training.advance_proficiency",
)
def advance_proficiency(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Deterministically advance a technician's proficiency level.

    This is the ONLY autonomous state mutation allowed for training.
    Updates TechnicianSkill.proficiency_level based on hours thresholds.
    Re-validates certification gates before applying the advancement.
    """
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        tech_skill_id = payload.entity_id
        new_level = payload.data.get("new_level")
        tech_id = payload.data.get("technician_id")

        tech_skill = session.get(TechnicianSkill, tech_skill_id)
        if not tech_skill:
            return {"status": "skipped", "reason": "TechnicianSkill not found"}

        technician = session.get(Technician, tech_id)
        if not technician:
            return {"status": "skipped", "reason": "Technician not found"}

        # Re-validate using advancement service (cert gates may have changed)
        result = evaluate_skill_advancement(session, technician, tech_skill)

        if not result.should_advance:
            logger.warning(
                "Advancement blocked for %s skill '%s': %s",
                technician.full_name,
                tech_skill.skill_name,
                result.blocked_reason,
            )
            return {
                "status": "blocked",
                "technician_id": str(tech_id),
                "skill": tech_skill.skill_name,
                "reason": result.blocked_reason,
            }

        old_level = _enum_val(tech_skill.proficiency_level)

        # Map string to enum for setting
        level_map = {v.value: v for v in ProficiencyLevel}
        tech_skill.proficiency_level = level_map.get(new_level, ProficiencyLevel.INTERMEDIATE)
        session.commit()

        # Create a suggested action for ops visibility
        cert_info = ""
        if result.cert_gate and result.cert_gate.is_satisfied:
            cert_info = f" (cert gate '{result.cert_gate.required_cert}' satisfied)"

        action = SuggestedAction(
            target_role="ops",
            action_type="training_advancement",
            title=f"Proficiency Advanced: {payload.data.get('skill_name', 'skill')}",
            description=(
                f"{technician.full_name} advanced from {old_level} to {new_level} in "
                f"{payload.data.get('skill_name', 'a skill')} "
                f"({payload.data.get('hours', 0):.0f} hours accumulated){cert_info}"
            ),
            link=f"/technicians/{tech_id}",
            priority=2,
        )
        session.add(action)
        session.commit()

        # ── WebSocket notification: broadcast level change to ops ──
        skill_name = payload.data.get("skill_name", tech_skill.skill_name)
        hours_val = payload.data.get("hours", tech_skill.training_hours_accumulated or 0)

        _broadcast_ws_notification("technicians", {
            "event_type": "training.proficiency_advanced",
            "topic": "technicians",
            "technician_id": str(tech_id),
            "technician_name": technician.full_name,
            "data": {
                "skill_name": skill_name,
                "old_level": old_level,
                "new_level": new_level,
                "hours_accumulated": hours_val,
                "message": (
                    f"{technician.full_name} advanced to {new_level} in "
                    f"{skill_name} ({hours_val:.0f} hours accumulated)"
                ),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Also push to dashboard topic for real-time ops updates
        _broadcast_ws_notification("dashboard", {
            "event_type": "dashboard.training_advancement",
            "topic": "dashboard",
            "data": {
                "technician_id": str(tech_id),
                "technician_name": technician.full_name,
                "skill_name": skill_name,
                "old_level": old_level,
                "new_level": new_level,
                "hours": hours_val,
                "message": (
                    f"{technician.full_name} advanced to {new_level} in {skill_name}"
                ),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        logger.info(
            "Proficiency advanced + WS notified: %s %s -> %s for skill %s",
            technician.full_name, old_level, new_level, skill_name,
        )

        # Cascade PROFICIENCY_ADVANCED for recommendation refresh
        cascade_events = [
            EventPayload(
                event_type=EventType.PROFICIENCY_ADVANCED,
                entity_type="technician",
                entity_id=str(tech_id),
                actor_id="system",
                data={
                    "skill_name": skill_name,
                    "old_level": old_level,
                    "new_level": new_level,
                },
            ).to_dict()
        ]

        return {
            "status": "advanced",
            "technician_id": str(tech_id),
            "technician_name": technician.full_name,
            "skill": skill_name,
            "old_level": old_level,
            "new_level": new_level,
            "ws_notification_sent": True,
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
    name="app.workers.tasks.training.update_deployability_for_training",
)
def update_deployability_for_training(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Recalculate deployability status after training progress."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        tech_id = payload.data.get("technician_id", payload.entity_id)
        technician = session.get(Technician, tech_id)
        if not technician:
            return {"status": "skipped", "reason": "Technician not found"}

        if technician.deployability_locked:
            return {"status": "skipped", "reason": "Deployability locked"}

        cs = _enum_val(technician.career_stage)
        if cs != CareerStage.IN_TRAINING.value:
            return {"status": "skipped", "reason": f"Not in training (stage={cs})"}

        all_intermediate = all(
            _enum_val(ts.proficiency_level)
            in (ProficiencyLevel.INTERMEDIATE.value, ProficiencyLevel.ADVANCED.value)
            for ts in technician.skills
        ) if technician.skills else False

        if all_intermediate and technician.skills:
            return {
                "status": "training_complete_candidate",
                "technician_id": str(tech_id),
                "cascade_events": [
                    EventPayload(
                        event_type=EventType.TRAINING_COMPLETED,
                        entity_type="technician",
                        entity_id=str(tech_id),
                        actor_id="system",
                        data={"reason": "All skills at Intermediate or above"},
                    ).to_dict()
                ],
            }

        session.commit()
        return {"status": "still_training", "technician_id": str(tech_id)}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.training.update_career_stage_training_complete",
)
def update_career_stage_training_complete(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Deterministically advance career stage from In Training to Training Completed."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        tech_id = payload.entity_id
        technician = session.get(Technician, tech_id)
        if not technician:
            return {"status": "skipped", "reason": "Technician not found"}

        cs = _enum_val(technician.career_stage)
        if cs == CareerStage.IN_TRAINING.value:
            old_stage = cs
            technician.career_stage = CareerStage.TRAINING_COMPLETED
            new_stage = CareerStage.TRAINING_COMPLETED.value
            session.commit()

            logger.info(
                "Career stage advanced: %s -> %s for technician %s (%s)",
                old_stage, new_stage, tech_id, technician.full_name,
            )

            # ── WebSocket notification: career stage change ──
            _broadcast_ws_notification("technicians", {
                "event_type": "training.career_stage_advanced",
                "topic": "technicians",
                "technician_id": str(tech_id),
                "technician_name": technician.full_name,
                "data": {
                    "old_stage": old_stage,
                    "new_stage": new_stage,
                    "message": (
                        f"{technician.full_name} completed training — "
                        f"ready for assignment"
                    ),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            _broadcast_ws_notification("dashboard", {
                "event_type": "dashboard.career_stage_change",
                "topic": "dashboard",
                "data": {
                    "technician_id": str(tech_id),
                    "technician_name": technician.full_name,
                    "old_stage": old_stage,
                    "new_stage": new_stage,
                    "message": (
                        f"{technician.full_name} completed training — "
                        f"ready for assignment"
                    ),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # Create suggested action for ops
            action = SuggestedAction(
                target_role="ops",
                action_type="training_completed",
                title=f"Training Completed: {technician.full_name}",
                description=(
                    f"{technician.full_name} has completed all training requirements "
                    f"and is ready for assignment."
                ),
                link=f"/technicians/{tech_id}",
                priority=1,
            )
            session.add(action)
            session.commit()

            return {
                "status": "advanced",
                "technician_id": str(tech_id),
                "technician_name": technician.full_name,
                "old_stage": old_stage,
                "new_stage": new_stage,
                "ws_notification_sent": True,
            }

        return {"status": "skipped", "reason": f"Career stage is {cs}, not In Training"}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.training.initialize_training_plan",
)
def initialize_training_plan(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Create initial suggested actions for a newly created technician."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        tech_id = payload.entity_id
        technician = session.get(Technician, tech_id)
        if not technician:
            return {"status": "skipped", "reason": "Technician not found"}

        action = SuggestedAction(
            target_role="ops",
            action_type="new_technician",
            title=f"New Technician: {technician.full_name}",
            description=f"Review and assign training plan for {technician.full_name} (stage: {_enum_val(technician.career_stage)})",
            link=f"/technicians/{tech_id}",
            priority=3,
        )
        session.add(action)
        session.commit()

        return {"status": "ok", "technician_id": str(tech_id), "action_created": True}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
