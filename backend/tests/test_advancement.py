"""Tests for the Advancement Evaluation Service.

Tests cover:
  - Apprentice → Intermediate promotion at 100 hours (default threshold)
  - Intermediate → Advanced promotion at 300 hours (default threshold)
  - Custom per-skill hour thresholds
  - Certification gate blocking when cert is missing
  - Certification gate blocking when cert is Expired/Pending
  - Certification gate passing when cert is Active
  - No advancement when already at Advanced level
  - No advancement when hours insufficient
  - Full technician evaluation with mixed skill states
  - evaluate_and_advance applies mutations correctly
"""

import uuid
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

# We test the service functions in isolation using mocked DB objects.
# No actual database is needed.

from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    ProficiencyLevel,
    CertStatus,
    CareerStage,
    DeployabilityStatus,
)
from app.models.skill import Skill, SkillCategory
from app.services.advancement import (
    evaluate_skill_advancement,
    evaluate_technician_advancement,
    evaluate_and_advance,
    _check_cert_gate,
    CertGateResult,
    SkillAdvancementResult,
    TechnicianAdvancementEvaluation,
    DEFAULT_INTERMEDIATE_HOURS,
    DEFAULT_ADVANCED_HOURS,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_technician(
    skills=None,
    certifications=None,
    tech_id=None,
) -> Technician:
    """Create a mock Technician with skills and certs."""
    t = Technician()
    t.id = tech_id or uuid.uuid4()
    t.first_name = "Test"
    t.last_name = "Technician"
    t.email = f"test-{t.id}@example.com"
    t.career_stage = CareerStage.IN_TRAINING
    t.deployability_status = DeployabilityStatus.IN_TRAINING
    t.skills = skills or []
    t.certifications = certifications or []
    return t


def _make_skill(
    name: str,
    level: ProficiencyLevel = ProficiencyLevel.APPRENTICE,
    hours: float = 0.0,
    skill_id=None,
) -> TechnicianSkill:
    """Create a mock TechnicianSkill."""
    ts = TechnicianSkill()
    ts.id = skill_id or uuid.uuid4()
    ts.skill_name = name
    ts.proficiency_level = level
    ts.training_hours_accumulated = hours
    return ts


def _make_cert(
    name: str,
    status: CertStatus = CertStatus.ACTIVE,
) -> TechnicianCertification:
    """Create a mock TechnicianCertification."""
    tc = TechnicianCertification()
    tc.id = uuid.uuid4()
    tc.cert_name = name
    tc.status = status
    tc.issue_date = date(2025, 1, 1)
    tc.expiry_date = date(2028, 1, 1)
    return tc


def _make_skill_def(
    name: str,
    intermediate_hours: int = 100,
    advanced_hours: int = 300,
    cert_gate_intermediate: str = None,
    cert_gate_advanced: str = None,
) -> Skill:
    """Create a mock Skill definition."""
    s = Skill()
    s.id = uuid.uuid4()
    s.name = name
    s.slug = name.lower().replace(" ", "-")
    s.intermediate_hours_threshold = intermediate_hours
    s.advanced_hours_threshold = advanced_hours
    s.cert_gate_intermediate = cert_gate_intermediate
    s.cert_gate_advanced = cert_gate_advanced
    s.is_active = True
    return s


def _mock_session_with_skill_def(skill_def):
    """Create a mock session that returns a skill definition."""
    session = MagicMock()
    query = MagicMock()
    query.filter.return_value.first.return_value = skill_def
    session.query.return_value = query
    return session


# ---------------------------------------------------------------------------
# Tests: _check_cert_gate
# ---------------------------------------------------------------------------

class TestCheckCertGate:
    def test_no_gate_configured_returns_none(self):
        tech = _make_technician()
        result = _check_cert_gate(tech, None)
        assert result is None

    def test_cert_active_satisfies_gate(self):
        tech = _make_technician(certifications=[
            _make_cert("FOA CFOT", CertStatus.ACTIVE),
        ])
        result = _check_cert_gate(tech, "FOA CFOT")
        assert result is not None
        assert result.is_satisfied is True
        assert result.required_cert == "FOA CFOT"
        assert result.cert_status == "Active"

    def test_cert_expired_blocks_gate(self):
        tech = _make_technician(certifications=[
            _make_cert("FOA CFOT", CertStatus.EXPIRED),
        ])
        result = _check_cert_gate(tech, "FOA CFOT")
        assert result is not None
        assert result.is_satisfied is False
        assert result.cert_status == "Expired"

    def test_cert_pending_blocks_gate(self):
        tech = _make_technician(certifications=[
            _make_cert("OSHA 10", CertStatus.PENDING),
        ])
        result = _check_cert_gate(tech, "OSHA 10")
        assert result is not None
        assert result.is_satisfied is False
        assert result.cert_status == "Pending"

    def test_cert_revoked_blocks_gate(self):
        tech = _make_technician(certifications=[
            _make_cert("BICSI Technician", CertStatus.REVOKED),
        ])
        result = _check_cert_gate(tech, "BICSI Technician")
        assert result.is_satisfied is False
        assert result.cert_status == "Revoked"

    def test_cert_missing_blocks_gate(self):
        tech = _make_technician(certifications=[])
        result = _check_cert_gate(tech, "FOA CFOT")
        assert result is not None
        assert result.is_satisfied is False
        assert result.cert_status is None

    def test_different_cert_does_not_satisfy_gate(self):
        tech = _make_technician(certifications=[
            _make_cert("OSHA 10", CertStatus.ACTIVE),
        ])
        result = _check_cert_gate(tech, "FOA CFOT")
        assert result.is_satisfied is False
        assert result.cert_status is None


# ---------------------------------------------------------------------------
# Tests: evaluate_skill_advancement
# ---------------------------------------------------------------------------

class TestEvaluateSkillAdvancement:
    def test_beginner_to_intermediate_at_100_hours(self):
        """Apprentice with 100+ hours should advance to Intermediate."""
        tech = _make_technician(skills=[
            _make_skill("Fiber Splicing", ProficiencyLevel.APPRENTICE, 120.0),
        ])
        skill_def = _make_skill_def("Fiber Splicing")
        session = _mock_session_with_skill_def(skill_def)

        result = evaluate_skill_advancement(session, tech, tech.skills[0])

        assert result.should_advance is True
        assert result.current_level == "Apprentice"
        assert result.target_level == "Intermediate"
        assert result.hours_met is True
        assert result.hours_accumulated == 120.0

    def test_intermediate_to_advanced_at_300_hours(self):
        """Intermediate with 300+ hours should advance to Advanced."""
        tech = _make_technician(skills=[
            _make_skill("Fiber Splicing", ProficiencyLevel.INTERMEDIATE, 350.0),
        ])
        skill_def = _make_skill_def("Fiber Splicing")
        session = _mock_session_with_skill_def(skill_def)

        result = evaluate_skill_advancement(session, tech, tech.skills[0])

        assert result.should_advance is True
        assert result.current_level == "Intermediate"
        assert result.target_level == "Advanced"

    def test_insufficient_hours_blocks_advancement(self):
        """Apprentice with <100 hours should not advance."""
        tech = _make_technician(skills=[
            _make_skill("Fiber Splicing", ProficiencyLevel.APPRENTICE, 50.0),
        ])
        skill_def = _make_skill_def("Fiber Splicing")
        session = _mock_session_with_skill_def(skill_def)

        result = evaluate_skill_advancement(session, tech, tech.skills[0])

        assert result.should_advance is False
        assert result.hours_met is False
        assert "50 more hours" in result.blocked_reason

    def test_advanced_level_no_further_advancement(self):
        """Already Advanced should not advance further."""
        tech = _make_technician(skills=[
            _make_skill("Fiber Splicing", ProficiencyLevel.ADVANCED, 500.0),
        ])
        skill_def = _make_skill_def("Fiber Splicing")
        session = _mock_session_with_skill_def(skill_def)

        result = evaluate_skill_advancement(session, tech, tech.skills[0])

        assert result.should_advance is False
        assert result.target_level is None
        assert "Already at Advanced" in result.blocked_reason

    def test_custom_hours_thresholds(self):
        """Skills with custom thresholds should use them."""
        tech = _make_technician(skills=[
            _make_skill("OTDR Testing", ProficiencyLevel.APPRENTICE, 90.0),
        ])
        # OTDR Testing has 80-hour intermediate threshold
        skill_def = _make_skill_def("OTDR Testing", intermediate_hours=80, advanced_hours=250)
        session = _mock_session_with_skill_def(skill_def)

        result = evaluate_skill_advancement(session, tech, tech.skills[0])

        assert result.should_advance is True
        assert result.hours_threshold == 80

    def test_cert_gate_blocks_intermediate_advancement(self):
        """Cert gate on intermediate should block even with enough hours."""
        tech = _make_technician(
            skills=[_make_skill("OTDR Testing", ProficiencyLevel.APPRENTICE, 100.0)],
            certifications=[],  # No certs
        )
        skill_def = _make_skill_def(
            "OTDR Testing",
            intermediate_hours=80,
            cert_gate_intermediate="FOA CFOT",
        )
        session = _mock_session_with_skill_def(skill_def)

        result = evaluate_skill_advancement(session, tech, tech.skills[0])

        assert result.should_advance is False
        assert result.hours_met is True
        assert result.cert_gate is not None
        assert result.cert_gate.required_cert == "FOA CFOT"
        assert result.cert_gate.is_satisfied is False
        assert "Missing required certification" in result.blocked_reason

    def test_cert_gate_blocks_advanced_with_expired_cert(self):
        """Expired cert should block advanced advancement."""
        tech = _make_technician(
            skills=[_make_skill("Fiber Splicing", ProficiencyLevel.INTERMEDIATE, 350.0)],
            certifications=[_make_cert("FOA CFOT", CertStatus.EXPIRED)],
        )
        skill_def = _make_skill_def(
            "Fiber Splicing",
            cert_gate_advanced="FOA CFOT",
        )
        session = _mock_session_with_skill_def(skill_def)

        result = evaluate_skill_advancement(session, tech, tech.skills[0])

        assert result.should_advance is False
        assert result.hours_met is True
        assert result.cert_gate.is_satisfied is False
        assert "Expired" in result.blocked_reason

    def test_cert_gate_passes_with_active_cert(self):
        """Active cert should allow advancement through cert gate."""
        tech = _make_technician(
            skills=[_make_skill("Fiber Splicing", ProficiencyLevel.INTERMEDIATE, 350.0)],
            certifications=[_make_cert("FOA CFOT", CertStatus.ACTIVE)],
        )
        skill_def = _make_skill_def(
            "Fiber Splicing",
            cert_gate_advanced="FOA CFOT",
        )
        session = _mock_session_with_skill_def(skill_def)

        result = evaluate_skill_advancement(session, tech, tech.skills[0])

        assert result.should_advance is True
        assert result.cert_gate is not None
        assert result.cert_gate.is_satisfied is True

    def test_no_skill_definition_uses_defaults(self):
        """When Skill definition not found, use default thresholds."""
        tech = _make_technician(skills=[
            _make_skill("Unknown Skill", ProficiencyLevel.APPRENTICE, 110.0),
        ])
        session = MagicMock()
        query = MagicMock()
        query.filter.return_value.first.return_value = None
        session.query.return_value = query

        result = evaluate_skill_advancement(session, tech, tech.skills[0])

        assert result.should_advance is True
        assert result.hours_threshold == DEFAULT_INTERMEDIATE_HOURS

    def test_safety_compliance_dual_cert_gates(self):
        """Safety & Compliance has cert gates at both levels."""
        # Test intermediate gate: needs OSHA 10
        tech = _make_technician(
            skills=[_make_skill("Safety & Compliance", ProficiencyLevel.APPRENTICE, 50.0)],
            certifications=[_make_cert("OSHA 10", CertStatus.ACTIVE)],
        )
        skill_def = _make_skill_def(
            "Safety & Compliance",
            intermediate_hours=40,
            advanced_hours=150,
            cert_gate_intermediate="OSHA 10",
            cert_gate_advanced="OSHA 30",
        )
        session = _mock_session_with_skill_def(skill_def)

        result = evaluate_skill_advancement(session, tech, tech.skills[0])
        assert result.should_advance is True
        assert result.cert_gate.required_cert == "OSHA 10"
        assert result.cert_gate.is_satisfied is True

    def test_safety_compliance_advanced_needs_osha_30(self):
        """Safety & Compliance advanced gate needs OSHA 30 (not just OSHA 10)."""
        tech = _make_technician(
            skills=[_make_skill("Safety & Compliance", ProficiencyLevel.INTERMEDIATE, 200.0)],
            certifications=[_make_cert("OSHA 10", CertStatus.ACTIVE)],  # Has OSHA 10 but not OSHA 30
        )
        skill_def = _make_skill_def(
            "Safety & Compliance",
            intermediate_hours=40,
            advanced_hours=150,
            cert_gate_intermediate="OSHA 10",
            cert_gate_advanced="OSHA 30",
        )
        session = _mock_session_with_skill_def(skill_def)

        result = evaluate_skill_advancement(session, tech, tech.skills[0])
        assert result.should_advance is False
        assert result.hours_met is True
        assert result.cert_gate.required_cert == "OSHA 30"
        assert result.cert_gate.is_satisfied is False


# ---------------------------------------------------------------------------
# Tests: evaluate_technician_advancement (full evaluation)
# ---------------------------------------------------------------------------

class TestEvaluateTechnicianAdvancement:
    def test_mixed_skill_states(self):
        """Technician with skills at different stages."""
        tech = _make_technician(
            skills=[
                _make_skill("Fiber Splicing", ProficiencyLevel.APPRENTICE, 120.0),
                _make_skill("OTDR Testing", ProficiencyLevel.INTERMEDIATE, 200.0),
                _make_skill("Copper Termination", ProficiencyLevel.ADVANCED, 400.0),
            ],
            certifications=[],
        )

        # Fiber Splicing: no cert gate, should advance
        # OTDR Testing: Intermediate with 200 hours (needs 300 for Advanced) — won't advance
        # Copper Termination: already Advanced — won't advance

        session = MagicMock()
        query = MagicMock()
        # Return None (no skill def) for all — uses defaults
        query.filter.return_value.first.return_value = None
        session.query.return_value = query

        evaluation = evaluate_technician_advancement(session, tech)

        assert len(evaluation.skill_results) == 3
        assert evaluation.has_advancements is True
        assert len(evaluation.advancements_ready) == 1
        assert evaluation.advancements_ready[0].skill_name == "Fiber Splicing"

    def test_all_blocked(self):
        """All skills blocked — no advancements."""
        tech = _make_technician(
            skills=[
                _make_skill("Fiber Splicing", ProficiencyLevel.APPRENTICE, 50.0),
                _make_skill("OTDR Testing", ProficiencyLevel.APPRENTICE, 30.0),
            ],
        )

        session = MagicMock()
        query = MagicMock()
        query.filter.return_value.first.return_value = None
        session.query.return_value = query

        evaluation = evaluate_technician_advancement(session, tech)

        assert evaluation.has_advancements is False
        assert len(evaluation.advancements_ready) == 0

    def test_no_skills(self):
        """Technician with no skills should return empty evaluation."""
        tech = _make_technician(skills=[])

        session = MagicMock()
        evaluation = evaluate_technician_advancement(session, tech)

        assert len(evaluation.skill_results) == 0
        assert evaluation.has_advancements is False


# ---------------------------------------------------------------------------
# Tests: evaluate_and_advance (applies mutations)
# ---------------------------------------------------------------------------

class TestEvaluateAndAdvance:
    def test_applies_advancement(self):
        """evaluate_and_advance should mutate proficiency level."""
        skill = _make_skill("Fiber Splicing", ProficiencyLevel.APPRENTICE, 120.0)
        tech = _make_technician(skills=[skill])

        session = MagicMock()
        session.get.side_effect = lambda model, pk: {
            Technician: tech,
            TechnicianSkill: skill,
        }.get(model)

        query = MagicMock()
        query.filter.return_value.first.return_value = None
        session.query.return_value = query

        evaluation = evaluate_and_advance(session, str(tech.id))

        assert evaluation.has_advancements is True
        assert skill.proficiency_level == ProficiencyLevel.INTERMEDIATE

    def test_does_not_advance_blocked_skill(self):
        """evaluate_and_advance should NOT mutate cert-gated skills."""
        skill = _make_skill("Fiber Splicing", ProficiencyLevel.INTERMEDIATE, 350.0)
        tech = _make_technician(skills=[skill], certifications=[])

        session = MagicMock()
        session.get.side_effect = lambda model, pk: {
            Technician: tech,
            TechnicianSkill: skill,
        }.get(model)

        skill_def = _make_skill_def("Fiber Splicing", cert_gate_advanced="FOA CFOT")
        query = MagicMock()
        query.filter.return_value.first.return_value = skill_def
        session.query.return_value = query

        evaluation = evaluate_and_advance(session, str(tech.id))

        # Should NOT have advanced — still Intermediate
        assert skill.proficiency_level == ProficiencyLevel.INTERMEDIATE
        assert len(evaluation.advancements_blocked) == 1

    def test_raises_for_missing_technician(self):
        """evaluate_and_advance should raise ValueError for unknown tech."""
        session = MagicMock()
        session.get.return_value = None

        with pytest.raises(ValueError, match="not found"):
            evaluate_and_advance(session, str(uuid.uuid4()))

    def test_advances_multiple_skills_at_once(self):
        """Multiple skills ready should all advance."""
        skill1 = _make_skill("Fiber Splicing", ProficiencyLevel.APPRENTICE, 150.0)
        skill2 = _make_skill("OTDR Testing", ProficiencyLevel.APPRENTICE, 120.0)
        skill3 = _make_skill("Copper Termination", ProficiencyLevel.INTERMEDIATE, 350.0)
        tech = _make_technician(skills=[skill1, skill2, skill3])

        session = MagicMock()
        session.get.side_effect = lambda model, pk: {
            Technician: tech,
            TechnicianSkill: next(
                (s for s in [skill1, skill2, skill3] if str(s.id) == str(pk)), None
            ),
        }.get(model)

        query = MagicMock()
        query.filter.return_value.first.return_value = None
        session.query.return_value = query

        evaluation = evaluate_and_advance(session, str(tech.id))

        assert len(evaluation.advancements_ready) == 3
        assert skill1.proficiency_level == ProficiencyLevel.INTERMEDIATE
        assert skill2.proficiency_level == ProficiencyLevel.INTERMEDIATE
        assert skill3.proficiency_level == ProficiencyLevel.ADVANCED


# ---------------------------------------------------------------------------
# Tests: TechnicianAdvancementEvaluation properties
# ---------------------------------------------------------------------------

class TestEvaluationProperties:
    def test_advancements_blocked_property(self):
        """advancements_blocked should return skills meeting hours but blocked."""
        eval_result = TechnicianAdvancementEvaluation(
            technician_id="test",
            technician_name="Test Tech",
            skill_results=[
                SkillAdvancementResult(
                    technician_skill_id="1",
                    skill_name="Fiber Splicing",
                    current_level="Intermediate",
                    target_level="Advanced",
                    hours_accumulated=350,
                    hours_threshold=300,
                    hours_met=True,
                    cert_gate=CertGateResult("FOA CFOT", False, None),
                    should_advance=False,
                    blocked_reason="Missing cert",
                ),
                SkillAdvancementResult(
                    technician_skill_id="2",
                    skill_name="OTDR Testing",
                    current_level="Apprentice",
                    target_level="Intermediate",
                    hours_accumulated=120,
                    hours_threshold=80,
                    hours_met=True,
                    should_advance=True,
                ),
            ],
        )

        assert len(eval_result.advancements_blocked) == 1
        assert eval_result.advancements_blocked[0].skill_name == "Fiber Splicing"
        assert len(eval_result.advancements_ready) == 1
        assert eval_result.advancements_ready[0].skill_name == "OTDR Testing"
