"""Staffing Sub-Agent Orchestrator.

Wires the two-stage ranking pipeline:
  Stage 1 — Pre-filter engine (SQL-based deterministic candidate scoring via prefilter_engine)
  Stage 2 — LLM Re-ranker (Claude-powered 5-dimension scorecard refinement)

Provides:
- End-to-end ranking for a project role or ad-hoc requirements
- Integration with existing prefilter_engine for Stage 1
- LLM re-ranking with automatic fallback to deterministic scores
- Recommendation persistence to the database
- Error handling with graceful degradation
- Batch ID tracking for recommendation lifecycle

This module is designed to be called from:
- The staffing API endpoint (on-demand ranking)
- Celery tasks (nightly batch refresh)
- The LangChain orchestrator agent (agent-initiated ranking)

Architecture contract:
    StaffingRequest → [PrefilterEngine] → PrefilterResult → [LLM Reranker] → StaffingResponse
                                                                ↓ (fallback)
                                                        Deterministic scores
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from sqlalchemy.orm import Session

from app.models.project import Project, ProjectRole
from app.models.recommendation import Recommendation, RecommendationStatus
from app.schemas.staffing import (
    StaffingRequest,
    StaffingResponse,
    CandidateRanking,
    Scorecard,
    ScorecardDimension,
)
from app.services.prefilter_engine import (
    run_prefilter as run_prefilter_engine,
    run_prefilter_batch as run_prefilter_batch_engine,
    PrefilterResult as EnginePrefilterResult,
    CandidateResult as EngineCandidateResult,
)
from app.services.reranker import RerankerInput, RerankerResult, run_reranker
from app.services.prefilter import PrefilterCandidate

logger = logging.getLogger("deployable.staffing.agent")


class StaffingAgentError(Exception):
    """Raised when the staffing agent cannot complete a request."""

    def __init__(self, message: str, detail: Optional[str] = None, fallback_available: bool = False):
        self.message = message
        self.detail = detail
        self.fallback_available = fallback_available
        super().__init__(message)


# ---------------------------------------------------------------------------
# Bridge: convert prefilter_engine results to reranker input
# ---------------------------------------------------------------------------

def _engine_candidate_to_prefilter_candidate(
    candidate: EngineCandidateResult,
    db: Session,
) -> PrefilterCandidate:
    """Convert an engine CandidateResult to a PrefilterCandidate for the reranker."""
    from app.models.technician import Technician
    from sqlalchemy.orm import joinedload

    tech = (
        db.query(Technician)
        .options(
            joinedload(Technician.skills),
            joinedload(Technician.certifications),
        )
        .filter(Technician.id == candidate.technician_id)
        .first()
    )

    if not tech:
        # Fallback with minimal data from the candidate result
        return PrefilterCandidate(
            technician_id=candidate.technician_id,
            technician_name=candidate.technician_name,
            skills={},
            certifications=[],
            home_base_city=None,
            home_base_state=None,
            approved_regions=[],
            available_from=None,
            years_experience=0,
            total_project_count=0,
            total_approved_hours=0,
            archetype=None,
            career_stage="Unknown",
            deployability_status="Unknown",
            willing_to_travel=True,
            skills_match_pct=candidate.scorecard.skills_match / 100.0,
            certs_match_pct=candidate.scorecard.cert_match / 100.0,
        )

    # Build skills map from technician
    skills_map: Dict[str, Dict[str, Any]] = {}
    for ts in tech.skills:
        level = ts.proficiency_level
        if hasattr(level, 'value'):
            level = level.value
        skills_map[ts.skill_name] = {
            "level": level,
            "hours": ts.training_hours_accumulated or 0,
        }

    # Build certs list
    certs_list = []
    for tc in tech.certifications:
        status = tc.status
        if hasattr(status, 'value'):
            status = status.value
        certs_list.append({
            "cert_name": tc.cert_name,
            "status": status,
            "expiry_date": str(tc.expiry_date) if tc.expiry_date else None,
        })

    career = tech.career_stage
    if hasattr(career, 'value'):
        career = career.value
    deploy = tech.deployability_status
    if hasattr(deploy, 'value'):
        deploy = deploy.value

    return PrefilterCandidate(
        technician_id=candidate.technician_id,
        technician_name=candidate.technician_name,
        skills=skills_map,
        certifications=certs_list,
        home_base_city=tech.home_base_city,
        home_base_state=tech.home_base_state,
        approved_regions=tech.approved_regions or [],
        available_from=tech.available_from,
        years_experience=tech.years_experience or 0,
        total_project_count=tech.total_project_count or 0,
        total_approved_hours=tech.total_approved_hours or 0,
        archetype=tech.archetype,
        career_stage=career,
        deployability_status=deploy,
        willing_to_travel=tech.willing_to_travel if tech.willing_to_travel is not None else True,
        skills_match_pct=candidate.scorecard.skills_match / 100.0,
        certs_match_pct=candidate.scorecard.cert_match / 100.0,
        region_match=candidate.scorecard.travel_fit >= 80.0,
        availability_match=candidate.scorecard.availability >= 80.0,
        preference_adjustments=[
            adj.get("reason", "")
            for adj in candidate.scorecard.preference_adjustments
        ],
    )


def _engine_result_to_deterministic_rankings(
    engine_result: EnginePrefilterResult,
) -> List[CandidateRanking]:
    """Convert prefilter_engine results directly to CandidateRanking (no LLM).

    Used as the deterministic fallback when the LLM is unavailable.
    """
    rankings = []
    for candidate in engine_result.candidates:
        sc = candidate.scorecard

        scorecard = Scorecard(
            skills_match=ScorecardDimension(
                name="Skills Match",
                score=round(sc.skills_match / 10.0, 1),  # Convert 0-100 to 0-10
                weight=2.0,
                rationale=_format_skill_rationale(sc.skill_details),
            ),
            certification_coverage=ScorecardDimension(
                name="Certification Coverage",
                score=round(sc.cert_match / 10.0, 1),
                weight=1.5,
                rationale=_format_cert_rationale(sc.cert_details),
            ),
            availability_fit=ScorecardDimension(
                name="Availability Fit",
                score=round(sc.availability / 10.0, 1),
                weight=1.0,
                rationale=f"Availability score: {sc.availability:.0f}/100",
            ),
            geographic_proximity=ScorecardDimension(
                name="Geographic Proximity",
                score=round(sc.travel_fit / 10.0, 1),
                weight=1.0,
                rationale=f"Travel fit score: {sc.travel_fit:.0f}/100",
            ),
            experience_depth=ScorecardDimension(
                name="Experience Depth",
                score=round(sc.experience / 10.0, 1),
                weight=1.0,
                rationale=f"Experience score: {sc.experience:.0f}/100",
            ),
        )

        # Build highlights and disqualifiers from engine data
        highlights = []
        disqualifiers = []

        met_skills = [s for s in sc.skill_details if s.met]
        if len(met_skills) == len(sc.skill_details) and sc.skill_details:
            highlights.append("Meets all required skills")
        elif met_skills:
            highlights.append(f"Meets {len(met_skills)}/{len(sc.skill_details)} required skills")

        all_certs_met = all(c.has_cert and c.is_active for c in sc.cert_details)
        if all_certs_met and sc.cert_details:
            highlights.append("Holds all required certifications")

        if sc.travel_fit >= 90:
            highlights.append("Local to project region")
        if sc.experience >= 70:
            highlights.append("Highly experienced")

        for adj in sc.preference_adjustments:
            if adj.get("modifier", 0) > 0:
                highlights.append(adj.get("reason", ""))
            else:
                disqualifiers.append(adj.get("reason", ""))

        missing_certs = [c for c in sc.cert_details if not c.has_cert]
        if missing_certs:
            disqualifiers.append(f"Missing certs: {', '.join(c.cert_name for c in missing_certs)}")

        rankings.append(CandidateRanking(
            rank=candidate.rank,
            technician_id=candidate.technician_id,
            technician_name=candidate.technician_name,
            overall_score=round(scorecard.weighted_total, 2),
            scorecard=scorecard,
            explanation=candidate.explanation,
            highlights=highlights,
            disqualifiers=disqualifiers,
        ))

    return rankings


def _format_skill_rationale(skill_details) -> str:
    """Format skill details into a concise rationale string."""
    parts = []
    for s in skill_details:
        if s.met:
            parts.append(f"{s.skill_name}: ✓ {s.technician_level}")
        else:
            level = s.technician_level or "missing"
            parts.append(f"{s.skill_name}: {level} (needs {s.required_level})")
    return "; ".join(parts) if parts else "No specific skills required"


def _format_cert_rationale(cert_details) -> str:
    """Format cert details into a concise rationale string."""
    parts = []
    for c in cert_details:
        if c.has_cert and c.is_active:
            parts.append(f"{c.cert_name}: ✓")
        elif c.has_cert:
            parts.append(f"{c.cert_name}: inactive")
        else:
            parts.append(f"{c.cert_name}: ✗")
    return "; ".join(parts) if parts else "No certs required"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _supersede_old_recommendations(
    db: Session,
    role_id: str,
    new_batch_id: str,
):
    """Mark previous pending recommendations for this role as superseded."""
    old_recs = (
        db.query(Recommendation)
        .filter(
            Recommendation.role_id == role_id,
            Recommendation.status == RecommendationStatus.PENDING.value,
            Recommendation.batch_id != new_batch_id,
        )
        .all()
    )
    for rec in old_recs:
        rec.status = RecommendationStatus.SUPERSEDED.value
        rec.updated_at = datetime.now(timezone.utc)

    if old_recs:
        logger.info(f"Superseded {len(old_recs)} old recommendations for role {role_id}")


def _persist_recommendations(
    db: Session,
    rankings: List[CandidateRanking],
    role_id: Optional[str],
    project_id: Optional[str],
    batch_id: str,
):
    """Persist ranked candidates as Recommendation records.

    These await human approval — no autonomous state mutations.
    """
    for ranking in rankings:
        rec = Recommendation(
            recommendation_type="staffing",
            target_entity_type="role" if role_id else "project",
            target_entity_id=role_id or project_id,
            role_id=role_id,
            project_id=project_id,
            technician_id=ranking.technician_id,
            rank=str(ranking.rank),
            overall_score=ranking.overall_score,
            scorecard=ranking.scorecard.to_dict(),
            explanation=ranking.explanation,
            status=RecommendationStatus.PENDING.value,
            agent_name="staffing_sub_agent",
            batch_id=batch_id,
            metadata_={
                "highlights": ranking.highlights,
                "disqualifiers": ranking.disqualifiers,
            },
        )
        db.add(rec)

    logger.info(f"Persisted {len(rankings)} recommendations (batch: {batch_id})")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def rank_candidates_for_role(
    db: Session,
    request: StaffingRequest,
) -> StaffingResponse:
    """Main orchestrator: pre-filter → LLM re-rank → persist.

    Two-stage pipeline:
    1. Uses prefilter_engine.run_prefilter() for deterministic SQL-based scoring
    2. Passes top candidates to LLM re-ranker for nuanced evaluation
    3. Falls back to deterministic scores if LLM unavailable

    Args:
        db: SQLAlchemy session
        request: StaffingRequest with role requirements

    Returns:
        StaffingResponse with ranked candidates, scorecards, and metadata

    Raises:
        StaffingAgentError: If the request cannot be completed
    """
    batch_id = str(uuid.uuid4())
    errors: List[str] = []

    logger.info(f"Staffing agent invoked (batch: {batch_id})")

    # ── Resolve role_id (required for prefilter_engine) ──
    role_id = request.role_id
    project_id = request.project_id

    if role_id:
        # Validate role exists
        role = db.query(ProjectRole).filter(ProjectRole.id == role_id).first()
        if not role:
            raise StaffingAgentError(
                message=f"ProjectRole '{role_id}' not found",
                detail="The specified role_id does not exist in the database",
            )
        project = db.query(Project).filter(Project.id == role.project_id).first()
        project_id = str(project.id) if project else None
        project_name = project.name if project else None
        role_name = role.role_name
        required_skills = role.required_skills or []
        required_certs = role.required_certs or []
        preferred_region = project.location_region if project else None
    elif request.required_skills or request.required_certs:
        # Ad-hoc query: need to find a suitable role or run custom pre-filter
        # For now, use our custom prefilter for ad-hoc requests
        role_name = None
        project_name = None
        required_skills = [
            {"skill": s.skill, "min_level": s.min_level}
            for s in request.required_skills
        ]
        required_certs = request.required_certs or []
        preferred_region = request.preferred_region
    else:
        raise StaffingAgentError(
            message="Must provide either role_id or inline requirements (required_skills/required_certs)",
        )

    # ── Stage 1: Pre-filter ──
    engine_result = None
    prefilter_candidates = []

    if role_id:
        try:
            logger.info(f"Running prefilter_engine for role_id={role_id}")
            engine_result = run_prefilter_engine(
                db=db,
                role_id=role_id,
                top_n=min(request.max_candidates * 2, 50),  # 2x headroom for re-ranking
                as_of_date=request.available_by,
            )
            logger.info(
                f"Pre-filter complete: {engine_result.total_shortlisted} candidates "
                f"from {engine_result.total_evaluated} evaluated "
                f"({engine_result.total_passed_hard_filter} passed hard filter)"
            )
        except ValueError as e:
            raise StaffingAgentError(message=str(e))
        except Exception as e:
            logger.error(f"Pre-filter engine failed: {e}")
            raise StaffingAgentError(
                message="Pre-filter stage failed",
                detail=str(e),
                fallback_available=False,
            )

        if not engine_result.candidates:
            return StaffingResponse(
                role_id=role_id,
                project_id=project_id,
                project_name=project_name,
                role_name=role_name,
                candidates=[],
                total_evaluated=engine_result.total_evaluated,
                total_prefiltered=0,
                batch_id=batch_id,
                preference_rules_applied=[],
                fallback_used=False,
                errors=["No candidates passed pre-filtering"],
            )

        # Convert engine candidates to reranker-compatible format
        for ec in engine_result.candidates:
            try:
                pc = _engine_candidate_to_prefilter_candidate(ec, db)
                prefilter_candidates.append(pc)
            except Exception as e:
                logger.warning(f"Failed to convert candidate {ec.technician_id}: {e}")
    else:
        # Ad-hoc mode: use our custom prefilter
        from app.services.prefilter import run_prefilter, PrefilterInput
        try:
            pf_input = PrefilterInput(
                required_skills=required_skills,
                required_certs=required_certs,
                preferred_region=preferred_region,
                available_by=request.available_by,
                max_candidates=min(request.max_candidates * 2, 50),
            )
            pf_result = run_prefilter(db, pf_input)
            prefilter_candidates = pf_result.candidates

            if not prefilter_candidates:
                return StaffingResponse(
                    role_id=None,
                    project_id=project_id,
                    project_name=None,
                    role_name=None,
                    candidates=[],
                    total_evaluated=pf_result.total_evaluated,
                    total_prefiltered=0,
                    batch_id=batch_id,
                    preference_rules_applied=pf_result.preference_rules_applied,
                    fallback_used=False,
                    errors=["No candidates passed pre-filtering"],
                )
        except Exception as e:
            logger.error(f"Custom pre-filter failed: {e}")
            raise StaffingAgentError(
                message="Pre-filter stage failed",
                detail=str(e),
            )

    # ── Stage 2: LLM Re-rank ──
    reranker_result: Optional[RerankerResult] = None
    fallback_used = False

    try:
        reranker_input = RerankerInput(
            candidates=prefilter_candidates,
            required_skills=required_skills,
            required_certs=required_certs,
            preferred_region=preferred_region,
            role_name=role_name if role_id else "Custom Query",
            project_name=project_name if role_id else None,
            include_explanation=request.include_explanation,
        )
        reranker_result = run_reranker(reranker_input)
        fallback_used = reranker_result.fallback_used
        errors.extend(reranker_result.errors)
    except Exception as e:
        logger.error(f"LLM re-ranker failed completely: {e}")
        errors.append(f"Re-ranker error: {str(e)}")
        fallback_used = True

    # ── Build final rankings ──
    final_rankings: List[CandidateRanking] = []

    if reranker_result and reranker_result.rankings:
        final_rankings = reranker_result.rankings
    elif engine_result and engine_result.candidates:
        # Pure deterministic fallback from engine results
        logger.info("Using deterministic engine scores as fallback")
        final_rankings = _engine_result_to_deterministic_rankings(engine_result)
        fallback_used = True
    elif prefilter_candidates:
        # Last resort: generate basic rankings from prefilter candidates
        from app.services.reranker import _deterministic_score
        for idx, candidate in enumerate(prefilter_candidates[:request.max_candidates], start=1):
            scorecard, highlights, disqualifiers = _deterministic_score(
                candidate, required_skills, required_certs, preferred_region,
            )
            final_rankings.append(CandidateRanking(
                rank=idx,
                technician_id=candidate.technician_id,
                technician_name=candidate.technician_name,
                overall_score=round(scorecard.weighted_total, 2),
                scorecard=scorecard,
                explanation=f"{candidate.technician_name} passed pre-filtering with {candidate.skills_match_pct:.0%} skills match.",
                highlights=highlights,
                disqualifiers=disqualifiers,
            ))
        fallback_used = True

    # Trim to requested max
    final_rankings = final_rankings[:request.max_candidates]

    # ── Stage 3: Persist as recommendations ──
    try:
        if role_id:
            _supersede_old_recommendations(db, role_id, batch_id)
        _persist_recommendations(db, final_rankings, role_id, project_id, batch_id)
        db.commit()
    except Exception as e:
        logger.error(f"Failed to persist recommendations: {e}")
        db.rollback()
        errors.append(f"Persistence error: {str(e)}")

    total_evaluated = engine_result.total_evaluated if engine_result else len(prefilter_candidates)
    total_prefiltered = engine_result.total_shortlisted if engine_result else len(prefilter_candidates)
    pref_rules = list(engine_result.weights_used.keys()) if engine_result else []

    return StaffingResponse(
        role_id=role_id,
        project_id=project_id,
        project_name=project_name if role_id else None,
        role_name=role_name if role_id else None,
        candidates=final_rankings,
        total_evaluated=total_evaluated,
        total_prefiltered=total_prefiltered,
        batch_id=batch_id,
        preference_rules_applied=pref_rules,
        fallback_used=fallback_used,
        errors=errors,
    )


def refresh_recommendations_for_role(
    db: Session,
    role_id: str,
    max_candidates: int = 10,
) -> StaffingResponse:
    """Refresh recommendations for a single role.

    Called by nightly batch or on-demand refresh.
    Supersedes old pending recommendations and generates new ones.
    """
    request = StaffingRequest(
        role_id=role_id,
        max_candidates=max_candidates,
        include_explanation=True,
        apply_preference_rules=True,
    )
    return rank_candidates_for_role(db, request)


def refresh_recommendations_for_project(
    db: Session,
    project_id: str,
    max_candidates_per_role: int = 10,
) -> List[StaffingResponse]:
    """Refresh recommendations for all open roles in a project.

    Uses prefilter_engine batch mode for efficiency.
    """
    results = []
    try:
        engine_results = run_prefilter_batch_engine(
            db=db,
            project_id=project_id,
            top_n=max_candidates_per_role,
        )
        for er in engine_results:
            request = StaffingRequest(
                role_id=er.role_id,
                project_id=project_id,
                max_candidates=max_candidates_per_role,
            )
            response = rank_candidates_for_role(db, request)
            results.append(response)
    except Exception as e:
        logger.error(f"Batch refresh failed for project {project_id}: {e}")
        raise StaffingAgentError(
            message=f"Batch refresh failed for project {project_id}",
            detail=str(e),
        )
    return results


def get_recommendations_for_role(
    db: Session,
    role_id: str,
    status: Optional[str] = None,
) -> List[dict]:
    """Retrieve persisted recommendations for a role."""
    query = db.query(Recommendation).filter(Recommendation.role_id == role_id)
    if status:
        query = query.filter(Recommendation.status == status)
    query = query.order_by(Recommendation.rank.asc())

    recs = query.all()
    return [
        {
            "id": str(rec.id),
            "technician_id": rec.technician_id,
            "rank": rec.rank,
            "overall_score": rec.overall_score,
            "scorecard": rec.scorecard,
            "explanation": rec.explanation,
            "status": rec.status,
            "batch_id": rec.batch_id,
            "highlights": (rec.metadata_ or {}).get("highlights", []),
            "disqualifiers": (rec.metadata_ or {}).get("disqualifiers", []),
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
        }
        for rec in recs
    ]


def handle_recommendation_action(
    db: Session,
    recommendation_id: str,
    action: str,
    rejection_reason: Optional[str] = None,
) -> dict:
    """Process a human approval/rejection of a recommendation.

    Actions: approve, reject, dismiss.
    This is the human approval gate — no autonomous state mutations.
    """
    rec = db.query(Recommendation).filter(
        Recommendation.id == recommendation_id
    ).first()

    if not rec:
        raise StaffingAgentError(message=f"Recommendation '{recommendation_id}' not found")

    if rec.status != RecommendationStatus.PENDING.value:
        raise StaffingAgentError(
            message=f"Recommendation is already '{rec.status}', cannot {action}"
        )

    now = datetime.now(timezone.utc)

    if action == "approve":
        rec.status = RecommendationStatus.APPROVED.value
        rec.updated_at = now
    elif action == "reject":
        rec.status = RecommendationStatus.REJECTED.value
        rec.rejection_reason = rejection_reason
        rec.updated_at = now
    elif action == "dismiss":
        rec.status = RecommendationStatus.DISMISSED.value
        rec.updated_at = now
    else:
        raise StaffingAgentError(message=f"Invalid action '{action}'. Use: approve, reject, dismiss")

    db.commit()

    return {
        "id": str(rec.id),
        "status": rec.status,
        "action": action,
        "updated_at": now.isoformat(),
    }
