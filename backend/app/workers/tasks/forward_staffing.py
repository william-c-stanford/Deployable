"""Forward Staffing background recommendation agent — Celery task.

Proactively analyzes upcoming assignment gaps within the 90-day window
and generates staffing recommendations, pushing updates via WebSocket
to the ops dashboard.

Scheduled via Celery Beat (every 6 hours) and can be triggered on-demand
via API endpoint.

Architecture:
  Celery Beat / API trigger
    → forward_staffing_scan task
      → ForwardStaffingService (gap analysis, candidate matching)
      → LangChain/Claude Haiku (NL explanations)
      → Recommendation persistence (requires human approval)
      → Redis pub/sub → WebSocket push to ops dashboard
"""

import json
import logging
from datetime import datetime, timezone, date, timedelta
from typing import Any, Optional

import redis

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload, EventType
from app.database import SessionLocal
from app.models.recommendation import Recommendation, RecommendationStatus
from app.models.audit import SuggestedAction, AuditLog
from app.models.project import ProjectRole, Project

logger = logging.getLogger("deployable.workers.forward_staffing")

# Redis channel for WebSocket relay
WS_BROADCAST_CHANNEL = "deployable:ws_broadcast"


def _get_redis():
    """Get a Redis client for WebSocket broadcast."""
    import os
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        return redis.from_url(redis_url, decode_responses=True)
    except Exception:
        logger.warning("Could not connect to Redis for WS broadcast")
        return None


def _broadcast_ws_event(topic: str, event_type: str, data: dict[str, Any]):
    """Publish a WebSocket event via Redis pub/sub.

    The FastAPI Redis relay listener picks this up and broadcasts
    to connected WebSocket clients on the specified topic.
    """
    r = _get_redis()
    if not r:
        return

    payload = {
        "topic": topic,
        "event": {
            "event_type": event_type,
            "topic": topic,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
    try:
        r.publish(WS_BROADCAST_CHANNEL, json.dumps(payload, default=str))
        logger.info("Broadcast WS event: %s on topic '%s'", event_type, topic)
    except Exception as e:
        logger.warning("Failed to broadcast WS event: %s", str(e))


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.forward_staffing.forward_staffing_scan",
    max_retries=2,
)
def forward_staffing_scan(self, event_dict: dict[str, Any] = None) -> dict[str, Any]:
    """Execute a forward staffing scan and generate proactive recommendations.

    This is the main background recommendation agent. It:
    1. Analyzes the 90-day window for assignment gaps
    2. Matches available technicians to gaps
    3. Generates LangChain/Claude explanations
    4. Creates Recommendation records (pending human approval)
    5. Creates SuggestedActions for the ops dashboard
    6. Pushes real-time updates via WebSocket

    Can be invoked:
    - By Celery Beat (every 6 hours)
    - By API endpoint (on-demand)
    - By event dispatch (after assignment changes)
    """
    if event_dict is None:
        event_dict = EventPayload(
            event_type=EventType.FORWARD_STAFFING_SCAN_TRIGGERED,
            entity_type="system",
            entity_id="forward_scan",
            actor_id="system",
        ).to_dict()

    session = SessionLocal()
    try:
        # Phase 1: Run the gap analysis
        from app.services.forward_staffing_service import (
            run_forward_staffing_scan,
            serialize_scan_result,
        )

        scan_result = run_forward_staffing_scan(session)
        serialized = serialize_scan_result(scan_result)

        # Phase 2: Generate LangChain-powered explanations
        try:
            import sys
            import os
            # Ensure the repo root is on sys.path so 'agents' package is importable
            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from agents.forward_staffing_chain import (
                generate_gap_analysis_summary,
                generate_gap_recommendation,
            )
        except ImportError:
            logger.warning("agents package not importable; using deterministic analysis only")
            # Fallback: use the service's built-in summary
            generate_gap_analysis_summary = None
            generate_gap_recommendation = None

        # Generate overall summary via LangChain/Claude Haiku
        if generate_gap_analysis_summary is None:
            llm_summary = scan_result.summary
        else:
            llm_summary = generate_gap_analysis_summary(
                gaps_data=serialized["gaps"],
                available_tech_count=serialized["available_technician_count"],
                scan_date=serialized["scan_date"],
                window_end=serialized["window_end"],
            )
        serialized["summary"] = llm_summary

        # Phase 3: Create recommendations for top gaps
        recommendations_created = []
        batch_id = f"forward_{date.today().isoformat()}_{datetime.now(timezone.utc).strftime('%H%M')}"

        for gap in scan_result.gaps[:15]:  # Process top 15 gaps
            if not gap.recommended_candidate_ids:
                continue

            role = session.get(ProjectRole, gap.role.role_id)
            project = session.get(Project, gap.role.project_id) if gap.role.project_id else None
            if not role:
                continue

            # Build candidate profiles for LLM recommendation
            candidate_profiles = [
                {
                    "full_name": at.full_name,
                    "available_from": at.available_from.isoformat(),
                    "home_base_city": at.home_base_city,
                    "home_base_state": at.home_base_state,
                    "career_stage": at.career_stage,
                    "skills": at.skills,
                    "certifications": at.certifications,
                }
                for at in gap.available_candidates[:5]
            ]

            # Generate gap-specific recommendation via LangChain
            gap_data_dict = {
                "gap_type": gap.gap_type,
                "role": {
                    "role_name": gap.role.role_name,
                    "project_name": gap.role.project_name,
                    "project_region": gap.role.project_region,
                    "gap_start_date": gap.role.gap_start_date.isoformat(),
                    "gap_slots": gap.role.gap_slots,
                    "urgency": gap.role.urgency,
                    "required_skills": gap.role.required_skills,
                    "required_certs": gap.role.required_certs,
                },
            }
            if generate_gap_recommendation is not None:
                gap_recommendation_text = generate_gap_recommendation(
                    gap_data=gap_data_dict,
                    candidate_profiles=candidate_profiles,
                )
            else:
                # Deterministic fallback
                top_name = candidate_profiles[0]["full_name"] if candidate_profiles else "No candidate"
                gap_recommendation_text = (
                    f"Forward staffing recommendation for {gap.role.role_name} on "
                    f"{gap.role.project_name} ({gap.role.urgency} urgency, "
                    f"{gap.role.gap_slots} slot(s)). Top candidate: {top_name}."
                )

            # Supersede old forward staffing recs for this role
            old_recs = session.query(Recommendation).filter(
                Recommendation.role_id == gap.role.role_id,
                Recommendation.recommendation_type == "forward_staffing",
                Recommendation.status == RecommendationStatus.PENDING.value,
            ).all()
            for old_rec in old_recs:
                old_rec.status = RecommendationStatus.SUPERSEDED.value
                old_rec.updated_at = datetime.now(timezone.utc)

            # Create recommendation records for each candidate
            for rank_idx, tech_id in enumerate(gap.recommended_candidate_ids[:5]):
                # Find matching available tech for details
                matching_tech = next(
                    (at for at in gap.available_candidates if at.technician_id == tech_id),
                    None,
                )

                rec = Recommendation(
                    recommendation_type="forward_staffing",
                    target_entity_type="technician",
                    target_entity_id=tech_id,
                    technician_id=tech_id,
                    role_id=gap.role.role_id,
                    project_id=gap.role.project_id,
                    rank=str(rank_idx + 1),
                    overall_score=gap.urgency_score,
                    scorecard={
                        "gap_type": gap.gap_type,
                        "urgency": gap.role.urgency,
                        "urgency_score": gap.urgency_score,
                        "gap_start_date": gap.role.gap_start_date.isoformat(),
                        "gap_slots": gap.role.gap_slots,
                        "candidate_available_from": (
                            matching_tech.available_from.isoformat()
                            if matching_tech else None
                        ),
                    },
                    explanation=gap_recommendation_text if rank_idx == 0 else (
                        f"Alternative candidate (rank {rank_idx + 1}) for "
                        f"{gap.role.role_name} on {gap.role.project_name}. "
                        f"{'Available from ' + matching_tech.available_from.isoformat() if matching_tech else 'Check availability.'}"
                    ),
                    status=RecommendationStatus.PENDING.value,
                    agent_name="forward_staffing_agent",
                    batch_id=batch_id,
                    metadata_={
                        "project_name": gap.role.project_name,
                        "role_name": gap.role.role_name,
                        "gap_type": gap.gap_type,
                        "urgency": gap.role.urgency,
                        "source": "forward_staffing_scan",
                        "scan_date": date.today().isoformat(),
                    },
                )
                session.add(rec)
                recommendations_created.append({
                    "technician_id": tech_id,
                    "role_id": gap.role.role_id,
                    "role_name": gap.role.role_name,
                    "project_name": gap.role.project_name,
                    "urgency": gap.role.urgency,
                    "rank": rank_idx + 1,
                })

        session.commit()

        # Phase 4: Create ops suggested actions for critical/high urgency gaps
        critical_gaps = [g for g in scan_result.gaps if g.role.urgency in ("critical", "high")]
        if critical_gaps:
            action = SuggestedAction(
                target_role="ops",
                action_type="forward_staffing_alert",
                title=f"Forward Staffing: {len(critical_gaps)} urgent gap(s) in 90-day window",
                description=llm_summary[:500],
                link="/dashboard?tab=forward-staffing",
                priority=8 if any(g.role.urgency == "critical" for g in critical_gaps) else 6,
                metadata_={
                    "scan_date": date.today().isoformat(),
                    "total_gaps": scan_result.total_gaps_found,
                    "gaps_by_urgency": scan_result.gaps_by_urgency,
                    "recommendations_created": len(recommendations_created),
                },
            )
            session.add(action)

            # Audit log
            audit = AuditLog(
                user_id="system",
                action="forward_staffing_scan",
                entity_type="system",
                entity_id="forward_scan",
                details={
                    "total_gaps": scan_result.total_gaps_found,
                    "gaps_by_urgency": scan_result.gaps_by_urgency,
                    "recommendations_created": len(recommendations_created),
                    "available_technicians": len(scan_result.available_technicians),
                },
                agent_name="forward_staffing_agent",
            )
            session.add(audit)
            session.commit()

        # Phase 5: Push WebSocket update to ops dashboard
        _broadcast_ws_event(
            topic="recommendations",
            event_type="forward_staffing.scan_complete",
            data={
                "scan_date": serialized["scan_date"],
                "total_gaps": scan_result.total_gaps_found,
                "gaps_by_urgency": scan_result.gaps_by_urgency,
                "recommendations_created": len(recommendations_created),
                "summary": llm_summary[:300],
                "top_gaps": serialized["gaps"][:5],
            },
        )

        # Also push to dashboard topic
        _broadcast_ws_event(
            topic="dashboard",
            event_type="forward_staffing.scan_complete",
            data={
                "total_gaps": scan_result.total_gaps_found,
                "gaps_by_urgency": scan_result.gaps_by_urgency,
                "recommendations_created": len(recommendations_created),
                "summary": llm_summary[:200],
            },
        )

        result = {
            "status": "completed",
            "scan_date": date.today().isoformat(),
            "total_gaps": scan_result.total_gaps_found,
            "gaps_by_urgency": scan_result.gaps_by_urgency,
            "recommendations_created": len(recommendations_created),
            "available_technicians": len(scan_result.available_technicians),
            "summary": llm_summary,
            "batch_id": batch_id,
        }

        logger.info(
            "Forward staffing scan complete: %d gaps, %d recommendations created",
            scan_result.total_gaps_found,
            len(recommendations_created),
        )

        return result

    except Exception:
        session.rollback()
        logger.exception("Forward staffing scan failed")
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.forward_staffing.refresh_forward_recommendations",
)
def refresh_forward_recommendations(self, event_dict: dict[str, Any] = None) -> dict[str, Any]:
    """Refresh forward staffing recommendations after an assignment change.

    Triggered by assignment-related events (created, ended, cancelled).
    Re-evaluates pending forward staffing recommendations to ensure
    they're still valid given the new assignment landscape.
    """
    if event_dict is None:
        event_dict = EventPayload(
            event_type=EventType.FORWARD_STAFFING_SCAN_TRIGGERED,
            entity_type="system",
            entity_id="refresh",
            actor_id="system",
        ).to_dict()

    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        # Get all pending forward staffing recommendations
        pending_recs = session.query(Recommendation).filter(
            Recommendation.recommendation_type == "forward_staffing",
            Recommendation.status == RecommendationStatus.PENDING.value,
        ).all()

        refreshed = 0
        superseded = 0

        for rec in pending_recs:
            if not rec.technician_id or not rec.role_id:
                continue

            from app.models.technician import Technician
            from app.models.assignment import Assignment

            tech = session.get(Technician, rec.technician_id)
            role = session.get(ProjectRole, rec.role_id)

            if not tech or not role:
                rec.status = RecommendationStatus.SUPERSEDED.value
                rec.updated_at = datetime.now(timezone.utc)
                superseded += 1
                continue

            # Check if technician is now assigned to this role (recommendation fulfilled)
            existing_assignment = session.query(Assignment).filter(
                Assignment.technician_id == tech.id,
                Assignment.role_id == role.id,
                Assignment.status.in_(["Active", "Pre-Booked"]),
            ).first()

            if existing_assignment:
                rec.status = RecommendationStatus.SUPERSEDED.value
                rec.explanation = "Superseded: technician already assigned to this role."
                rec.updated_at = datetime.now(timezone.utc)
                superseded += 1
                continue

            # Check if technician is now unavailable (assigned elsewhere)
            new_assignment = session.query(Assignment).filter(
                Assignment.technician_id == tech.id,
                Assignment.status.in_(["Active", "Pre-Booked"]),
            ).first()

            if new_assignment and rec.scorecard:
                gap_start = rec.scorecard.get("gap_start_date")
                if gap_start and new_assignment.end_date:
                    from datetime import date as date_cls
                    try:
                        gap_date = date_cls.fromisoformat(gap_start)
                        if new_assignment.end_date > gap_date:
                            rec.status = RecommendationStatus.SUPERSEDED.value
                            rec.explanation = (
                                f"Superseded: technician assigned to another role "
                                f"until {new_assignment.end_date.isoformat()}"
                            )
                            rec.updated_at = datetime.now(timezone.utc)
                            superseded += 1
                            continue
                    except (ValueError, TypeError):
                        pass

            refreshed += 1

        session.commit()

        # Broadcast refresh event
        _broadcast_ws_event(
            topic="recommendations",
            event_type="forward_staffing.recommendations_refreshed",
            data={
                "refreshed": refreshed,
                "superseded": superseded,
                "trigger": payload.event_type.value,
            },
        )

        return {
            "status": "refreshed",
            "pending_reviewed": len(pending_recs),
            "refreshed": refreshed,
            "superseded": superseded,
        }

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
