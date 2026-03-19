"""Recommendation generation reactive agent tasks.

This is the core of the staffing intelligence system. Handles:
  - HEADCOUNT_REQUESTED / ROLE_UNFILLED: Generate ranked staffing recommendations
  - PROFICIENCY_ADVANCED / STATUS_CHANGED / etc.: Refresh affected recommendations
  - RECOMMENDATION_APPROVED: Execute approved recommendation
  - RECOMMENDATION_REJECTED: Propose preference rules from feedback
  - PREFERENCE_RULE changes: Re-evaluate existing recommendations

Uses the scoring engine (app.services.scoring) for 5-dimension scorecards
and LangChain/Claude (app.services.agent_llm) for NL explanations.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload, EventType
from app.database import SessionLocal
from app.models.technician import Technician, DeployabilityStatus
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.assignment import Assignment
from app.models.recommendation import Recommendation, PreferenceRule, RecommendationStatus
from app.models.audit import SuggestedAction, AuditLog
from app.services.scoring import rank_technicians_for_role, score_technician_for_role
from app.services.agent_llm import (
    generate_staffing_explanation,
    generate_rejection_rule_suggestion,
)
from app.services.smart_merge import (
    smart_merge_for_role,
    smart_merge_for_technician,
    smart_merge_on_preference_rule_change,
)
from app.services.ws_broadcast import (
    publish_recommendation_update,
    publish_recommendation_list_refresh,
    publish_badge_count_update,
    publish_notification,
)

logger = logging.getLogger("deployable.workers.recommendation")


def _get_pending_count(session) -> int:
    """Get current count of pending recommendations for badge updates."""
    return session.query(Recommendation).filter(
        Recommendation.status == RecommendationStatus.PENDING.value,
    ).count()


def _enum_val(v):
    return v.value if hasattr(v, "value") else str(v) if v else ""


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.recommendation.generate_staffing_recommendations",
)
def generate_staffing_recommendations(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Generate ranked staffing recommendations for a project role.

    Invoked when:
      - HEADCOUNT_REQUESTED: New headcount request from partner
      - ROLE_UNFILLED: Existing role has unfilled slots

    Scores all eligible technicians, generates NL explanations via
    LangChain/Claude, and persists as Recommendation records.
    """
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        role_id = payload.data.get("role_id", payload.entity_id)
        role = session.get(ProjectRole, role_id)
        if not role:
            return {"status": "skipped", "reason": f"ProjectRole {role_id} not found"}

        project = session.get(Project, role.project_id)
        project_name = project.name if project else ""

        # Get already-assigned or dismissed technicians for this role
        existing_recs = session.query(Recommendation).filter(
            Recommendation.role_id == str(role.id),
            Recommendation.recommendation_type == "staffing",
            Recommendation.status.in_([
                RecommendationStatus.APPROVED.value,
                RecommendationStatus.DISMISSED.value,
            ]),
        ).all()
        exclude_ids = {
            r.target_entity_id for r in existing_recs if r.target_entity_id
        }

        # Also exclude currently assigned techs on this role
        current_assignments = session.query(Assignment).filter(
            Assignment.role_id == role.id,
            Assignment.status == "Active",
        ).all()
        for a in current_assignments:
            exclude_ids.add(str(a.technician_id))

        # Supersede any existing pending recommendations for this role
        pending_recs = session.query(Recommendation).filter(
            Recommendation.role_id == str(role.id),
            Recommendation.recommendation_type == "staffing",
            Recommendation.status == RecommendationStatus.PENDING.value,
        ).all()
        for rec in pending_recs:
            rec.status = RecommendationStatus.SUPERSEDED.value
            rec.updated_at = datetime.now(timezone.utc)

        # Score and rank candidates
        ranked = rank_technicians_for_role(
            session, role, project,
            limit=10,
            exclude_ids=exclude_ids,
        )

        recommendations_created = []
        for rank_idx, scorecard in enumerate(ranked):
            tech_id = scorecard["technician_id"]
            technician = session.get(Technician, tech_id)
            tech_name = technician.full_name if technician else "Unknown"

            # Generate NL explanation via LangChain/Claude
            explanation = generate_staffing_explanation(
                scorecard=scorecard,
                technician_name=tech_name,
                role_name=role.role_name,
                project_name=project_name,
            )

            rec = Recommendation(
                recommendation_type="staffing",
                target_entity_type="technician",
                target_entity_id=str(tech_id),
                technician_id=str(tech_id),
                role_id=str(role.id),
                project_id=str(role.project_id),
                rank=str(rank_idx + 1),
                overall_score=scorecard["overall_score"],
                scorecard=scorecard,
                explanation=explanation,
                status=RecommendationStatus.PENDING.value,
                agent_name="staffing_agent",
                metadata_={
                    "project_name": project_name,
                    "role_name": role.role_name,
                },
            )
            session.add(rec)
            recommendations_created.append({
                "technician_id": str(tech_id),
                "technician_name": tech_name,
                "overall_score": scorecard["overall_score"],
                "rank": rank_idx + 1,
            })

        session.commit()

        # Create ops suggested action if recommendations were generated
        if recommendations_created:
            action = SuggestedAction(
                target_role="ops",
                action_type="staffing_recommendations",
                title=f"Staffing: {role.role_name} ({len(recommendations_created)} candidates)",
                description=(
                    f"New staffing recommendations for {role.role_name}"
                    f"{' on ' + project_name if project_name else ''}. "
                    f"Top candidate: {recommendations_created[0]['technician_name']} "
                    f"({recommendations_created[0]['overall_score']}/100)."
                ),
                link=f"/projects/{role.project_id}/roles/{role.id}/recommendations",
                priority=5,
            )
            session.add(action)
            session.commit()

        logger.info(
            "Generated %d staffing recommendations for role %s",
            len(recommendations_created), role_id,
        )

        # --- WebSocket broadcast: new recommendations created ---
        pending_count = _get_pending_count(session)
        publish_recommendation_list_refresh(
            role_id=str(role_id),
            project_id=str(role.project_id) if role else None,
            summary={
                "action": "generated",
                "count": len(recommendations_created),
                "role_name": role.role_name if role else "",
                "project_name": project_name,
                "top_candidate": recommendations_created[0] if recommendations_created else None,
            },
            pending_count=pending_count,
        )
        publish_badge_count_update(
            badge_type="pending_recommendations",
            count=pending_count,
            role="ops",
        )
        if recommendations_created:
            publish_notification(
                notification_type="staffing_recommendations",
                title=f"New Staffing Recommendations",
                message=(
                    f"{len(recommendations_created)} candidates ranked for "
                    f"{role.role_name if role else 'role'}"
                    f"{' on ' + project_name if project_name else ''}"
                ),
                role="ops",
                severity="info",
                link=f"/agent-inbox",
                entity_type="project_role",
                entity_id=str(role_id),
            )

        return {
            "status": "generated",
            "role_id": str(role_id),
            "project_name": project_name,
            "candidates": recommendations_created,
            "superseded": len(pending_recs),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.recommendation.refresh_affected_recommendations",
)
def refresh_affected_recommendations(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Refresh recommendations affected by a technician/project change.

    Uses the smart merge algorithm to:
    - Update existing pending recommendations in-place (preserving context)
    - Supersede disqualified candidates with clear reasons
    - Track score deltas for ops visibility
    - Never touch dismissed/approved/rejected recommendations

    For project/role changes, runs a full smart merge that can also add
    new qualifying candidates.
    """
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        entity_type = payload.entity_type
        entity_id = payload.entity_id
        total_updated = 0
        total_superseded = 0

        if entity_type == "technician":
            # Smart merge for a single technician across all their pending recs
            merge_stats = smart_merge_for_technician(
                session=session,
                technician_id=entity_id,
                generate_explanation_fn=generate_staffing_explanation,
            )
            total_updated = merge_stats.get("updated", 0)
            total_superseded = merge_stats.get("superseded", 0)

        elif entity_type in ("project", "project_role"):
            # For project/role changes, run full smart merge per affected role
            if entity_type == "project_role":
                role_ids = [str(entity_id)]
            else:
                roles = session.query(ProjectRole).filter(
                    ProjectRole.project_id == entity_id
                ).all()
                role_ids = [str(r.id) for r in roles]

            for role_id in role_ids:
                role = session.get(ProjectRole, role_id)
                if not role:
                    continue
                project = session.get(Project, role.project_id)

                # Re-score all eligible technicians for this role
                from app.services.smart_merge import (
                    _get_terminal_tech_ids,
                    _get_assigned_tech_ids,
                )
                terminal_ids = _get_terminal_tech_ids(session, role_id)
                assigned_ids = _get_assigned_tech_ids(session, role_id)
                exclude_ids = terminal_ids | assigned_ids

                evaluations = rank_technicians_for_role(
                    session, role, project,
                    limit=15,
                    exclude_ids=exclude_ids,
                )

                merge_result = smart_merge_for_role(
                    session=session,
                    role_id=role_id,
                    new_evaluations=evaluations,
                    recommendation_type="staffing",
                    agent_name="refresh_agent",
                    generate_explanation_fn=generate_staffing_explanation,
                )
                total_updated += merge_result.updated
                total_superseded += merge_result.superseded

            session.commit()

        refreshed = total_updated + total_superseded

        # --- WebSocket broadcast: recommendations refreshed ---
        if refreshed > 0:
            pending_count = _get_pending_count(session)
            publish_recommendation_list_refresh(
                summary={
                    "action": "smart_merge_refreshed",
                    "entity_type": entity_type,
                    "entity_id": str(entity_id),
                    "updated": total_updated,
                    "superseded": total_superseded,
                },
                pending_count=pending_count,
            )
            publish_badge_count_update(
                badge_type="pending_recommendations",
                count=pending_count,
                role="ops",
            )

        return {
            "status": "refreshed",
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "recommendations_updated": total_updated,
            "recommendations_superseded": total_superseded,
            "recommendations_refreshed": refreshed,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.recommendation.execute_approved_recommendation",
)
def execute_approved_recommendation(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Process an approved recommendation (human-approved, not autonomous).

    For staffing recommendations, this creates a suggested action to
    finalize the assignment — it does NOT auto-create assignments.
    Human approval gate integrity is maintained.
    """
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        rec_id = payload.entity_id
        rec = session.get(Recommendation, rec_id)
        if not rec:
            return {"status": "skipped", "reason": "Recommendation not found"}

        if rec.recommendation_type == "staffing":
            tech = session.get(Technician, rec.target_entity_id) if rec.target_entity_id else None
            role = session.get(ProjectRole, rec.role_id) if rec.role_id else None
            tech_name = tech.full_name if tech else "Unknown"
            role_name = role.role_name if role else "Unknown"

            # Create action to finalize assignment (requires another human click)
            action = SuggestedAction(
                target_role="ops",
                action_type="finalize_assignment",
                title=f"Finalize: {tech_name} → {role_name}",
                description=(
                    f"Recommendation approved. Create assignment for {tech_name} "
                    f"to {role_name}. Review dates and rates before confirming."
                ),
                link=f"/assignments/new?tech_id={rec.target_entity_id}&role_id={rec.role_id}",
                priority=5,
            )
            session.add(action)

            audit = AuditLog(
                user_id=payload.actor_id,
                action="recommendation_approved",
                entity_type="recommendation",
                entity_id=str(rec_id),
                details={
                    "type": rec.recommendation_type,
                    "technician_id": rec.target_entity_id,
                    "role_id": rec.role_id,
                },
                agent_name="recommendation_agent",
            )
            session.add(audit)

        elif rec.recommendation_type == "cert_renewal":
            action = SuggestedAction(
                target_role="ops",
                action_type="schedule_cert_renewal",
                title="Schedule Cert Renewal",
                description="Approved cert renewal recommendation. Schedule with technician.",
                link=f"/technicians/{rec.target_entity_id}",
                priority=4,
            )
            session.add(action)

        elif rec.recommendation_type == "backfill":
            if rec.role_id:
                cascade_events = [
                    EventPayload(
                        event_type=EventType.ROLE_UNFILLED,
                        entity_type="project_role",
                        entity_id=str(rec.role_id),
                        actor_id=payload.actor_id,
                        data={"role_id": str(rec.role_id), "reason": "backfill_approved"},
                    ).to_dict()
                ]
                session.commit()
                return {
                    "status": "executed",
                    "recommendation_id": str(rec_id),
                    "type": rec.recommendation_type,
                    "cascade_events": cascade_events,
                }

        session.commit()

        # --- WebSocket broadcast: recommendation executed ---
        pending_count = _get_pending_count(session)
        publish_recommendation_update(
            event_type="recommendation.executed",
            recommendation_data={
                "id": str(rec_id),
                "type": rec.recommendation_type,
                "status": rec.status,
            },
            pending_count=pending_count,
        )
        publish_badge_count_update(
            badge_type="pending_recommendations",
            count=pending_count,
            role="ops",
        )

        return {
            "status": "executed",
            "recommendation_id": str(rec_id),
            "type": rec.recommendation_type,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.recommendation.handle_rejection_feedback",
)
def handle_rejection_feedback(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Process rejection feedback to propose editable preference rules.

    When ops rejects a recommendation, this task:
    1. Records the rejection reason on the recommendation
    2. Analyzes the rejection reason to match a predefined rule template
    3. Creates a PreferenceRule with status='proposed' and suggested parameters
    4. Creates a SuggestedAction for ops to review/edit/approve the proposed rule
    5. Logs an audit entry for the rejection
    """
    from app.services.preference_rule_proposer import propose_preference_rule

    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        rec_id = payload.entity_id
        rec = session.get(Recommendation, rec_id)
        if not rec:
            return {"status": "skipped", "reason": "Recommendation not found"}

        rejection_reason = payload.data.get("rejection_reason") or payload.data.get("reason", "")
        if not rejection_reason:
            rejection_reason = rec.rejection_reason or "No reason provided"

        tech_name = "Unknown"
        role_name = "Unknown"
        technician = None
        role = None

        if rec.target_entity_id:
            technician = session.get(Technician, rec.target_entity_id)
            tech_name = technician.full_name if technician else "Unknown"
        if rec.role_id:
            role = session.get(ProjectRole, rec.role_id)
            role_name = role.role_name if role else "Unknown"

        # Store rejection reason on the recommendation if not already set
        if not rec.rejection_reason:
            rec.rejection_reason = rejection_reason

        # ── Core: propose a preference rule from rejection analysis ──
        proposed_rule = propose_preference_rule(
            session=session,
            recommendation=rec,
            rejection_reason=rejection_reason,
            technician=technician,
            role=role,
        )

        # Also generate LLM explanation for richer context (best-effort)
        rule_suggestion_text = generate_rejection_rule_suggestion(
            rejection_reason=rejection_reason,
            technician_name=tech_name,
            role_name=role_name,
        )

        # Create ops action to review the proposed rule
        action = SuggestedAction(
            target_role="ops",
            action_type="review_preference_rule",
            title=f"Review Proposed Rule: {proposed_rule.rule_type}",
            description=(
                f"Based on rejection of {tech_name} for {role_name}: "
                f"Agent proposes a '{proposed_rule.rule_type}' rule "
                f"(effect: {proposed_rule.effect}, threshold: {proposed_rule.threshold}). "
                f"{rule_suggestion_text or ''}"
            ),
            link="/settings/preference-rules?status=proposed",
            priority=3,
            metadata_={
                "rejection_reason": rejection_reason,
                "recommendation_id": str(rec_id),
                "proposed_rule_id": str(proposed_rule.id),
                "rule_type": proposed_rule.rule_type,
                "template_type": proposed_rule.template_type,
                "threshold": proposed_rule.threshold,
                "effect": proposed_rule.effect,
                "parameters": proposed_rule.parameters,
                "technician_name": tech_name,
                "role_name": role_name,
                "llm_suggestion": rule_suggestion_text,
            },
        )
        session.add(action)

        # Audit log
        audit = AuditLog(
            user_id=payload.actor_id,
            action="recommendation_rejected_rule_proposed",
            entity_type="recommendation",
            entity_id=str(rec_id),
            details={
                "reason": rejection_reason,
                "proposed_rule_id": str(proposed_rule.id),
                "proposed_rule_type": proposed_rule.rule_type,
                "proposed_template_type": proposed_rule.template_type,
                "proposed_effect": proposed_rule.effect,
                "proposed_threshold": proposed_rule.threshold,
                "proposed_parameters": proposed_rule.parameters,
            },
            agent_name="rejection_learning_agent",
        )
        session.add(audit)
        session.commit()

        # --- WebSocket broadcast: rejection feedback processed ---
        pending_count = _get_pending_count(session)
        publish_recommendation_update(
            event_type="recommendation.rejected",
            recommendation_data={
                "id": str(rec_id),
                "type": rec.recommendation_type,
                "status": rec.status,
                "rejection_reason": rejection_reason,
                "proposed_rule_id": str(proposed_rule.id),
                "proposed_rule_type": proposed_rule.rule_type,
            },
            pending_count=pending_count,
        )
        publish_badge_count_update(
            badge_type="pending_recommendations",
            count=pending_count,
            role="ops",
        )
        publish_notification(
            notification_type="preference_rule_proposed",
            title="New Preference Rule Proposed",
            message=(
                f"Based on rejection of {tech_name}: "
                f"'{proposed_rule.rule_type}' rule proposed "
                f"(effect: {proposed_rule.effect}, threshold: {proposed_rule.threshold})"
            ),
            role="ops",
            severity="info",
            link="/settings/preference-rules?status=proposed",
        )

        return {
            "status": "feedback_processed",
            "recommendation_id": str(rec_id),
            "rejection_reason": rejection_reason,
            "proposed_rule": {
                "id": str(proposed_rule.id),
                "rule_type": proposed_rule.rule_type,
                "template_type": proposed_rule.template_type,
                "effect": proposed_rule.effect,
                "threshold": proposed_rule.threshold,
                "parameters": proposed_rule.parameters,
                "status": proposed_rule.status,
            },
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.recommendation.reeval_recommendations_for_rule",
)
def reeval_recommendations_for_rule(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Re-evaluate all pending recommendations when a preference rule changes.

    Uses the smart merge algorithm with preference-rule-aware scoring to:
    - Update scores in-place with new preference rule effects
    - Supersede candidates now excluded by the new rules
    - Preserve context and score history through the change
    """
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        merge_stats = smart_merge_on_preference_rule_change(
            session=session,
            generate_explanation_fn=generate_staffing_explanation,
        )

        reevaluated = merge_stats.get("reevaluated", 0)
        updated = merge_stats.get("updated", 0)
        superseded = merge_stats.get("superseded", 0)
        unchanged = merge_stats.get("unchanged", 0)

        logger.info(
            "Smart merge re-evaluated %d recommendations (%d updated, %d superseded, "
            "%d unchanged) for rule change",
            reevaluated, updated, superseded, unchanged,
        )

        # --- WebSocket broadcast: rule-triggered re-evaluation ---
        if reevaluated > 0:
            pending_count = _get_pending_count(session)
            publish_recommendation_list_refresh(
                summary={
                    "action": "smart_merge_rule_reevaluated",
                    "reevaluated": reevaluated,
                    "updated": updated,
                    "superseded": superseded,
                    "unchanged": unchanged,
                    "rule_event": payload.event_type.value,
                },
                pending_count=pending_count,
            )
            publish_badge_count_update(
                badge_type="pending_recommendations",
                count=pending_count,
                role="ops",
            )
            if superseded > 0:
                publish_notification(
                    notification_type="rule_reevaluation",
                    title="Preference Rule Applied",
                    message=(
                        f"Smart merge re-evaluated {reevaluated} recommendations: "
                        f"{updated} updated, {superseded} superseded"
                    ),
                    role="ops",
                    severity="info",
                    link="/agent-inbox",
                )

        return {
            "status": "reevaluated",
            "total_reevaluated": reevaluated,
            "updated": updated,
            "superseded": superseded,
            "unchanged": unchanged,
            "rule_event": payload.event_type.value,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
