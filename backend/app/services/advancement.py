"""Advancement Evaluation Service — deterministic skill level promotions.

Evaluates technician skills for automatic proficiency advancement based on
approved training hours, with optional per-skill certification gates.

Thresholds (configurable per skill, defaults):
  - Apprentice → Intermediate: 100 approved hours
  - Intermediate → Advanced: 300 approved hours

Certification gates are optional per-skill overrides defined on the Skill
model (cert_gate_intermediate, cert_gate_advanced). When set, the technician
must hold the named certification with an Active status to advance, in
addition to meeting the hours threshold.

This service performs DETERMINISTIC evaluation only — it reads state and
returns advancement decisions. The actual state mutation is done by the
caller (typically a Celery task) after reviewing the result.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    ProficiencyLevel,
    CertStatus,
)
from app.models.skill import Skill

logger = logging.getLogger("deployable.services.advancement")

# Default hour thresholds (used when Skill record has no custom value)
DEFAULT_INTERMEDIATE_HOURS = 100
DEFAULT_ADVANCED_HOURS = 300


@dataclass
class CertGateResult:
    """Result of a certification gate check."""
    required_cert: str
    is_satisfied: bool
    cert_status: Optional[str] = None  # e.g. "Active", "Expired", or None if not found


@dataclass
class SkillAdvancementResult:
    """Evaluation result for a single technician skill."""
    technician_skill_id: str
    skill_name: str
    current_level: str
    target_level: Optional[str] = None
    hours_accumulated: float = 0.0
    hours_threshold: float = 0.0
    hours_met: bool = False
    cert_gate: Optional[CertGateResult] = None
    should_advance: bool = False
    blocked_reason: Optional[str] = None


@dataclass
class TechnicianAdvancementEvaluation:
    """Full evaluation result for a technician's advancement status."""
    technician_id: str
    technician_name: str
    skill_results: list[SkillAdvancementResult] = field(default_factory=list)

    @property
    def advancements_ready(self) -> list[SkillAdvancementResult]:
        """Skills ready to be advanced."""
        return [r for r in self.skill_results if r.should_advance]

    @property
    def advancements_blocked(self) -> list[SkillAdvancementResult]:
        """Skills meeting hours but blocked by cert gate."""
        return [
            r for r in self.skill_results
            if r.hours_met and not r.should_advance and r.target_level is not None
        ]

    @property
    def has_advancements(self) -> bool:
        return len(self.advancements_ready) > 0


def _enum_val(v) -> str:
    """Safely extract .value from an enum, or return str."""
    return v.value if hasattr(v, "value") else str(v) if v else ""


def _get_skill_definition(session: Session, skill_name: str) -> Optional[Skill]:
    """Look up the Skill definition by name to get custom thresholds and cert gates."""
    return session.query(Skill).filter(Skill.name == skill_name).first()


def _check_cert_gate(
    technician: Technician,
    required_cert_name: Optional[str],
) -> Optional[CertGateResult]:
    """Check whether a technician holds the required certification with Active status.

    Returns None if no certification gate is configured.
    """
    if not required_cert_name:
        return None

    # Search technician's certifications for a matching active cert
    for tc in technician.certifications:
        if tc.cert_name == required_cert_name:
            cert_status = _enum_val(tc.status)
            return CertGateResult(
                required_cert=required_cert_name,
                is_satisfied=(cert_status == CertStatus.ACTIVE.value),
                cert_status=cert_status,
            )

    # Certification not found on technician at all
    return CertGateResult(
        required_cert=required_cert_name,
        is_satisfied=False,
        cert_status=None,
    )


def evaluate_skill_advancement(
    session: Session,
    technician: Technician,
    tech_skill: TechnicianSkill,
) -> SkillAdvancementResult:
    """Evaluate whether a single technician skill qualifies for level advancement.

    Checks:
    1. Hours accumulated vs threshold (per-skill or default)
    2. Optional certification gate (per-skill, must be Active)

    Args:
        session: Active database session.
        technician: The technician being evaluated.
        tech_skill: The specific TechnicianSkill record.

    Returns:
        SkillAdvancementResult with detailed evaluation outcome.
    """
    hours = tech_skill.training_hours_accumulated or 0.0
    current_level = _enum_val(tech_skill.proficiency_level)

    # Look up skill definition for custom thresholds and cert gates
    skill_def = _get_skill_definition(session, tech_skill.skill_name)

    # Determine target level and applicable threshold/cert gate
    target_level: Optional[str] = None
    hours_threshold: float = 0.0
    cert_gate_name: Optional[str] = None

    if current_level == ProficiencyLevel.APPRENTICE.value:
        target_level = ProficiencyLevel.INTERMEDIATE.value
        hours_threshold = (
            skill_def.intermediate_hours_threshold
            if skill_def and skill_def.intermediate_hours_threshold is not None
            else DEFAULT_INTERMEDIATE_HOURS
        )
        cert_gate_name = skill_def.cert_gate_intermediate if skill_def else None

    elif current_level == ProficiencyLevel.INTERMEDIATE.value:
        target_level = ProficiencyLevel.ADVANCED.value
        hours_threshold = (
            skill_def.advanced_hours_threshold
            if skill_def and skill_def.advanced_hours_threshold is not None
            else DEFAULT_ADVANCED_HOURS
        )
        cert_gate_name = skill_def.cert_gate_advanced if skill_def else None

    else:
        # Already Advanced — no further advancement
        return SkillAdvancementResult(
            technician_skill_id=str(tech_skill.id),
            skill_name=tech_skill.skill_name,
            current_level=current_level,
            target_level=None,
            hours_accumulated=hours,
            hours_threshold=0.0,
            hours_met=False,
            cert_gate=None,
            should_advance=False,
            blocked_reason="Already at Advanced level",
        )

    # Check hours threshold
    hours_met = hours >= hours_threshold

    # Check certification gate (if configured)
    cert_gate_result = _check_cert_gate(technician, cert_gate_name)

    # Determine if advancement should proceed
    should_advance = False
    blocked_reason = None

    if not hours_met:
        blocked_reason = (
            f"Needs {hours_threshold - hours:.0f} more hours "
            f"({hours:.0f}/{hours_threshold:.0f})"
        )
    elif cert_gate_result and not cert_gate_result.is_satisfied:
        if cert_gate_result.cert_status is None:
            blocked_reason = (
                f"Missing required certification: {cert_gate_result.required_cert}"
            )
        else:
            blocked_reason = (
                f"Certification '{cert_gate_result.required_cert}' status is "
                f"{cert_gate_result.cert_status} (must be Active)"
            )
    else:
        should_advance = True

    return SkillAdvancementResult(
        technician_skill_id=str(tech_skill.id),
        skill_name=tech_skill.skill_name,
        current_level=current_level,
        target_level=target_level,
        hours_accumulated=hours,
        hours_threshold=hours_threshold,
        hours_met=hours_met,
        cert_gate=cert_gate_result,
        should_advance=should_advance,
        blocked_reason=blocked_reason,
    )


def evaluate_technician_advancement(
    session: Session,
    technician: Technician,
) -> TechnicianAdvancementEvaluation:
    """Evaluate all skills for a technician and return advancement decisions.

    Args:
        session: Active database session.
        technician: The technician to evaluate.

    Returns:
        TechnicianAdvancementEvaluation with per-skill results.
    """
    evaluation = TechnicianAdvancementEvaluation(
        technician_id=str(technician.id),
        technician_name=technician.full_name,
    )

    for tech_skill in technician.skills:
        result = evaluate_skill_advancement(session, technician, tech_skill)
        evaluation.skill_results.append(result)

    return evaluation


def evaluate_and_advance(
    session: Session,
    technician_id: str,
) -> TechnicianAdvancementEvaluation:
    """Evaluate a technician and apply all approved advancements.

    This is the main entry point for the advancement service. It:
    1. Loads the technician with skills and certifications
    2. Evaluates each skill for advancement eligibility
    3. Applies level promotions for skills that pass both hours AND cert gates
    4. Returns the full evaluation for audit/logging

    Args:
        session: Active database session.
        technician_id: UUID of the technician to evaluate.

    Returns:
        TechnicianAdvancementEvaluation with all results.

    Raises:
        ValueError: If technician not found.
    """
    technician = session.get(Technician, technician_id)
    if not technician:
        raise ValueError(f"Technician {technician_id} not found")

    evaluation = evaluate_technician_advancement(session, technician)

    # Apply advancements
    level_map = {v.value: v for v in ProficiencyLevel}
    for result in evaluation.advancements_ready:
        tech_skill = session.get(TechnicianSkill, result.technician_skill_id)
        if tech_skill and result.target_level:
            old_level = _enum_val(tech_skill.proficiency_level)
            tech_skill.proficiency_level = level_map[result.target_level]
            logger.info(
                "Advanced %s skill '%s': %s -> %s (%.0f hours)",
                technician.full_name,
                result.skill_name,
                old_level,
                result.target_level,
                result.hours_accumulated,
            )

    return evaluation
