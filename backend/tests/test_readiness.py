"""Tests for the technician readiness re-evaluation service.

Tests cover:
  - Certification readiness scoring
  - Training progress readiness scoring
  - Assignment history readiness scoring
  - Documentation readiness scoring
  - Composite score calculation with correct weights
  - Suggested status determination logic
  - Batch evaluation
  - Edge cases (no data, locked status, all expired, etc.)
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import date, timedelta, datetime, timezone
import uuid

from app.services.readiness import (
    _evaluate_certification_readiness,
    _evaluate_training_readiness,
    _evaluate_assignment_history,
    _evaluate_documentation_readiness,
    _determine_suggested_status,
    evaluate_technician_readiness,
    evaluate_all_technicians_readiness,
    apply_readiness_status_update,
    READINESS_WEIGHTS,
    ReadinessResult,
    CertificationReadiness,
    TrainingReadiness,
    AssignmentHistoryReadiness,
    DocumentationReadiness,
    AssignmentHistoryDetail,
)


# ---------------------------------------------------------------------------
# Helpers — create mock objects matching the split model structure
# ---------------------------------------------------------------------------


def make_tech_skill(skill_name: str, proficiency: str, hours: float = 0.0):
    """Create a mock TechnicianSkill."""
    skill = MagicMock()
    skill.id = str(uuid.uuid4())
    skill.skill_name = skill_name
    skill.proficiency_level = proficiency
    skill.training_hours_accumulated = hours
    return skill


def make_cert(cert_name: str, status: str = "Active", expiry_date=None):
    """Create a mock TechnicianCertification."""
    cert = MagicMock()
    cert.id = str(uuid.uuid4())
    cert.cert_name = cert_name
    cert.status = status
    cert.expiry_date = expiry_date
    return cert


def make_document(doc_type: str, verification_status: str = "Verified"):
    """Create a mock TechnicianDocument."""
    doc = MagicMock()
    doc.id = str(uuid.uuid4())
    doc.doc_type = doc_type
    doc.verification_status = verification_status
    return doc


def make_enrollment(status: str = "Active", total_hours: float = 50.0):
    """Create a mock TrainingEnrollment."""
    enrollment = MagicMock()
    enrollment.id = str(uuid.uuid4())
    enrollment.status = status
    enrollment.total_hours_logged = total_hours
    return enrollment


def make_assignment(
    status: str = "Completed",
    start_date=None,
    end_date=None,
    role_id=None,
):
    """Create a mock Assignment."""
    assignment = MagicMock()
    assignment.id = str(uuid.uuid4())
    assignment.status = status
    assignment.start_date = start_date or date.today() - timedelta(days=90)
    assignment.end_date = end_date or date.today() - timedelta(days=30)
    assignment.role = MagicMock()
    assignment.role.project_id = role_id or str(uuid.uuid4())
    assignment.role_id = assignment.role.project_id
    assignment.technician_id = str(uuid.uuid4())
    return assignment


def make_technician(
    tech_id=None,
    name="John Doe",
    career_stage="Deployed",
    deployability_status="Ready Now",
    skills=None,
    certifications=None,
    documents=None,
    training_enrollments=None,
    docs_verified=True,
    deployability_locked=False,
):
    tech = MagicMock()
    tech.id = tech_id or str(uuid.uuid4())
    tech.full_name = name
    tech.first_name = name.split()[0]
    tech.last_name = name.split()[-1]
    tech.career_stage = career_stage
    tech.deployability_status = deployability_status
    tech.skills = skills or []
    tech.certifications = certifications or []
    tech.documents = documents or []
    tech.training_enrollments = training_enrollments or []
    tech.training_hours_logs = []
    tech.docs_verified = docs_verified
    tech.deployability_locked = deployability_locked
    tech.available_from = None
    tech.home_base_city = "Atlanta"
    tech.approved_regions = ["Southeast"]
    tech.total_approved_hours = 200
    return tech


# ---------------------------------------------------------------------------
# Certification Readiness Tests
# ---------------------------------------------------------------------------

class TestCertificationReadiness:
    def test_no_certs_baseline_score(self):
        """Technician with no certs gets baseline 50 score."""
        tech = make_technician(certifications=[])
        session = MagicMock()
        result = _evaluate_certification_readiness(session, tech)
        assert result.score == 50.0
        assert result.total_certs == 0

    def test_all_active_certs_full_score(self):
        """All active certs with no expiry gives ~100 score."""
        certs = [
            make_cert("FOA CFOT", "Active"),
            make_cert("OSHA 10", "Active"),
            make_cert("BICSI Technician", "Active"),
        ]
        tech = make_technician(certifications=certs)
        session = MagicMock()
        result = _evaluate_certification_readiness(session, tech)
        assert result.score >= 95.0
        assert result.active_certs == 3
        assert result.expired_certs == 0

    def test_expired_cert_reduces_score(self):
        """Expired cert contributes 0, reducing overall score."""
        certs = [
            make_cert("FOA CFOT", "Active"),
            make_cert("OSHA 10", "Expired", date.today() - timedelta(days=30)),
        ]
        tech = make_technician(certifications=certs)
        session = MagicMock()
        result = _evaluate_certification_readiness(session, tech)
        assert result.score < 60  # Active = 50 points, Expired = 0
        assert result.expired_certs == 1
        assert result.active_certs == 1

    def test_pending_cert_partial_credit(self):
        """Pending cert gets partial credit (30%)."""
        certs = [make_cert("OSHA 10", "Pending")]
        tech = make_technician(certifications=certs)
        session = MagicMock()
        result = _evaluate_certification_readiness(session, tech)
        assert result.score == pytest.approx(30.0, abs=1.0)
        assert result.pending_certs == 1

    def test_expiring_soon_cert_penalty(self):
        """Cert expiring within 30 days gets a penalty."""
        certs = [
            make_cert("FOA CFOT", "Active", date.today() + timedelta(days=10)),
        ]
        tech = make_technician(certifications=certs)
        session = MagicMock()
        result = _evaluate_certification_readiness(session, tech)
        assert result.expiring_soon_certs == 1
        assert result.score < 100.0  # Penalized

    def test_mixed_cert_statuses(self):
        """Mixed certs: active, expired, pending scored correctly."""
        certs = [
            make_cert("FOA CFOT", "Active"),
            make_cert("OSHA 10", "Expired", date.today() - timedelta(days=60)),
            make_cert("BICSI", "Pending"),
        ]
        tech = make_technician(certifications=certs)
        session = MagicMock()
        result = _evaluate_certification_readiness(session, tech)
        assert result.active_certs == 1
        assert result.expired_certs == 1
        assert result.pending_certs == 1
        assert len(result.details) == 3

    def test_revoked_cert_zero_contribution(self):
        """Revoked cert contributes 0 score."""
        certs = [make_cert("OSHA 10", "Revoked")]
        tech = make_technician(certifications=certs)
        session = MagicMock()
        result = _evaluate_certification_readiness(session, tech)
        assert result.score == 0.0
        assert result.expired_certs == 1  # Counted as expired


# ---------------------------------------------------------------------------
# Training Readiness Tests
# ---------------------------------------------------------------------------

class TestTrainingReadiness:
    def test_no_skills_low_score(self):
        """No skills gives a low baseline score."""
        tech = make_technician(skills=[])
        session = MagicMock()
        result = _evaluate_training_readiness(session, tech)
        assert result.score == 20.0
        assert result.total_skills == 0

    def test_all_advanced_high_score(self):
        """All Advanced skills give maximum training readiness."""
        skills = [
            make_tech_skill("Fiber Splicing", "Advanced", 400),
            make_tech_skill("OTDR Testing", "Advanced", 350),
        ]
        tech = make_technician(skills=skills)
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        result = _evaluate_training_readiness(session, tech)
        assert result.score >= 70  # High score for all advanced
        assert result.advanced_skills == 2
        assert result.apprentice_skills == 0

    def test_apprentice_skills_lower_score(self):
        """Apprentice-level skills produce a lower score."""
        skills = [
            make_tech_skill("Fiber Splicing", "Apprentice", 20),
            make_tech_skill("OTDR Testing", "Apprentice", 15),
        ]
        tech = make_technician(skills=skills)
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        result = _evaluate_training_readiness(session, tech)
        assert result.score < 50  # Lower score
        assert result.apprentice_skills == 2

    def test_mixed_proficiency_levels(self):
        """Mixed proficiency levels produce intermediate score."""
        skills = [
            make_tech_skill("Fiber Splicing", "Advanced", 350),
            make_tech_skill("OTDR Testing", "Intermediate", 150),
            make_tech_skill("Cable Pulling", "Apprentice", 30),
        ]
        tech = make_technician(skills=skills)
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        result = _evaluate_training_readiness(session, tech)
        assert result.advanced_skills == 1
        assert result.intermediate_skills == 1
        assert result.apprentice_skills == 1
        assert 30 < result.score < 80

    def test_training_hours_bonus(self):
        """High training hours add bonus points."""
        skills = [
            make_tech_skill("Fiber Splicing", "Intermediate", 500),
        ]
        tech = make_technician(skills=skills)
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        result = _evaluate_training_readiness(session, tech)
        assert result.total_training_hours == 500

    def test_enrollment_completed_bonus(self):
        """Completed training enrollments boost score."""
        skills = [make_tech_skill("Fiber Splicing", "Intermediate", 150)]
        enrollments = [
            make_enrollment("Completed", 100),
            make_enrollment("Completed", 200),
        ]
        tech = make_technician(skills=skills, training_enrollments=enrollments)
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        result = _evaluate_training_readiness(session, tech)
        assert result.completed_enrollments == 2
        # Score should be higher than without enrollments

    def test_skill_hours_to_next_level(self):
        """Details include hours needed for next level."""
        skills = [
            make_tech_skill("Fiber Splicing", "Apprentice", 60),
        ]
        tech = make_technician(skills=skills)
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        result = _evaluate_training_readiness(session, tech)
        detail = result.details[0]
        assert detail.next_level == "Intermediate"
        assert detail.hours_to_next_level == 40.0  # 100 - 60


# ---------------------------------------------------------------------------
# Assignment History Readiness Tests
# ---------------------------------------------------------------------------

class TestAssignmentHistoryReadiness:
    def test_no_assignments_baseline(self):
        """No assignment history gives baseline score."""
        tech = make_technician()
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = []
        result = _evaluate_assignment_history(session, tech)
        assert result.score == 25.0

    def test_completed_assignments_boost(self):
        """Completed assignments increase the score."""
        assignments = [
            make_assignment("Completed", date.today() - timedelta(days=120), date.today() - timedelta(days=60)),
            make_assignment("Completed", date.today() - timedelta(days=60), date.today() - timedelta(days=10)),
        ]
        tech = make_technician()
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = assignments
        result = _evaluate_assignment_history(session, tech)
        assert result.score > 25.0
        assert result.details.completed_assignments == 2

    def test_active_assignment_recency_bonus(self):
        """Active assignment provides recency bonus."""
        assignments = [
            make_assignment("Active", date.today() - timedelta(days=30), None),
        ]
        # Active assignment has no end_date; mock appropriately
        assignments[0].end_date = None
        tech = make_technician()
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = assignments
        result = _evaluate_assignment_history(session, tech)
        assert result.details.active_assignments == 1

    def test_high_completion_rate_bonus(self):
        """100% completion rate adds bonus."""
        assignments = [
            make_assignment("Completed"),
            make_assignment("Completed"),
            make_assignment("Completed"),
        ]
        tech = make_technician()
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = assignments
        result = _evaluate_assignment_history(session, tech)
        assert result.details.completion_rate == 1.0

    def test_cancelled_assignments_lower_rate(self):
        """Cancelled assignments lower completion rate."""
        assignments = [
            make_assignment("Completed"),
            make_assignment("Cancelled"),
        ]
        tech = make_technician()
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = assignments
        result = _evaluate_assignment_history(session, tech)
        assert result.details.completion_rate == 0.5

    def test_project_diversity_bonus(self):
        """Assignments across multiple projects boost diversity score."""
        assignments = [
            make_assignment("Completed", role_id="proj-1"),
            make_assignment("Completed", role_id="proj-2"),
            make_assignment("Completed", role_id="proj-3"),
        ]
        tech = make_technician()
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = assignments
        result = _evaluate_assignment_history(session, tech)
        assert result.details.unique_projects == 3


# ---------------------------------------------------------------------------
# Documentation Readiness Tests
# ---------------------------------------------------------------------------

class TestDocumentationReadiness:
    def test_no_docs_with_verified_flag(self):
        """No docs tracked but docs_verified=True gives full score."""
        tech = make_technician(documents=[], docs_verified=True)
        result = _evaluate_documentation_readiness(tech)
        assert result.score == 100.0

    def test_no_docs_unverified_baseline(self):
        """No docs tracked and unverified gives baseline score."""
        tech = make_technician(documents=[], docs_verified=False)
        result = _evaluate_documentation_readiness(tech)
        assert result.score == 50.0

    def test_all_docs_verified_full_score(self):
        """All docs verified gives high score."""
        docs = [
            make_document("Background Check", "Verified"),
            make_document("Government ID", "Verified"),
            make_document("Drug Test", "Verified"),
        ]
        tech = make_technician(documents=docs)
        result = _evaluate_documentation_readiness(tech)
        assert result.score >= 75.0
        assert result.verified_docs == 3

    def test_missing_docs_lower_score(self):
        """Missing/unsubmitted docs reduce score."""
        docs = [
            make_document("Background Check", "Verified"),
            make_document("Government ID", "Not Submitted"),
        ]
        tech = make_technician(documents=docs)
        result = _evaluate_documentation_readiness(tech)
        assert result.score < 60
        assert result.missing_docs == 1


# ---------------------------------------------------------------------------
# Status Determination Tests
# ---------------------------------------------------------------------------

class TestStatusDetermination:
    def test_active_assignment_currently_assigned(self):
        """Technician with active assignments → Currently Assigned."""
        tech = make_technician(deployability_status="Ready Now")
        cert = CertificationReadiness(score=80)
        training = TrainingReadiness(score=70)
        assignment = AssignmentHistoryReadiness(
            score=80,
            details=AssignmentHistoryDetail(active_assignments=1),
        )
        docs = DocumentationReadiness(score=100)

        status, should_change, reason = _determine_suggested_status(
            tech, cert, training, assignment, docs, 80.0
        )
        assert status == "Currently Assigned"
        assert should_change is True

    def test_expired_certs_missing_cert(self):
        """Expired certs → Missing Cert status."""
        tech = make_technician(deployability_status="Ready Now")
        cert = CertificationReadiness(score=30, expired_certs=1)
        training = TrainingReadiness(score=70)
        assignment = AssignmentHistoryReadiness(
            score=50,
            details=AssignmentHistoryDetail(),
        )
        docs = DocumentationReadiness(score=100)

        status, should_change, reason = _determine_suggested_status(
            tech, cert, training, assignment, docs, 60.0
        )
        assert status == "Missing Cert"
        assert should_change is True

    def test_missing_docs_status(self):
        """Missing docs → Missing Docs status."""
        tech = make_technician(deployability_status="Ready Now")
        cert = CertificationReadiness(score=80, expired_certs=0)
        training = TrainingReadiness(score=70)
        assignment = AssignmentHistoryReadiness(
            score=50,
            details=AssignmentHistoryDetail(),
        )
        docs = DocumentationReadiness(score=20, total_docs=3, missing_docs=2)

        status, should_change, reason = _determine_suggested_status(
            tech, cert, training, assignment, docs, 60.0
        )
        assert status == "Missing Docs"
        assert should_change is True

    def test_in_training_career_stage(self):
        """In Training career stage → In Training status."""
        tech = make_technician(
            career_stage="In Training",
            deployability_status="Ready Now",
        )
        cert = CertificationReadiness(score=80, expired_certs=0)
        training = TrainingReadiness(score=50)
        assignment = AssignmentHistoryReadiness(
            score=25,
            details=AssignmentHistoryDetail(),
        )
        docs = DocumentationReadiness(score=100, missing_docs=0)

        status, should_change, reason = _determine_suggested_status(
            tech, cert, training, assignment, docs, 55.0
        )
        assert status == "In Training"
        assert should_change is True

    def test_ready_now_status(self):
        """Completed training + high score → Ready Now."""
        tech = make_technician(
            career_stage="Training Completed",
            deployability_status="In Training",
        )
        cert = CertificationReadiness(score=90, expired_certs=0)
        training = TrainingReadiness(score=80)
        assignment = AssignmentHistoryReadiness(
            score=50,
            details=AssignmentHistoryDetail(),
        )
        docs = DocumentationReadiness(score=100, missing_docs=0)

        status, should_change, reason = _determine_suggested_status(
            tech, cert, training, assignment, docs, 75.0
        )
        assert status == "Ready Now"
        assert should_change is True

    def test_locked_status_no_change(self):
        """Locked deployability status → no change suggested."""
        tech = make_technician(
            deployability_status="In Training",
            deployability_locked=True,
        )
        cert = CertificationReadiness(score=90, expired_certs=0)
        training = TrainingReadiness(score=80)
        assignment = AssignmentHistoryReadiness(
            score=50,
            details=AssignmentHistoryDetail(),
        )
        docs = DocumentationReadiness(score=100)

        status, should_change, reason = _determine_suggested_status(
            tech, cert, training, assignment, docs, 75.0
        )
        assert should_change is False

    def test_already_at_suggested_status_no_change(self):
        """If already at the suggested status, no change recommended."""
        tech = make_technician(
            career_stage="Deployed",
            deployability_status="Ready Now",
        )
        cert = CertificationReadiness(score=90, expired_certs=0)
        training = TrainingReadiness(score=80)
        assignment = AssignmentHistoryReadiness(
            score=50,
            details=AssignmentHistoryDetail(),
        )
        docs = DocumentationReadiness(score=100, missing_docs=0)

        status, should_change, reason = _determine_suggested_status(
            tech, cert, training, assignment, docs, 75.0
        )
        assert status == "Ready Now"
        assert should_change is False


# ---------------------------------------------------------------------------
# Composite Score / Integration Tests
# ---------------------------------------------------------------------------

class TestCompositeScore:
    def test_weights_sum_to_one(self):
        """Verify readiness weights sum to 1.0."""
        assert sum(READINESS_WEIGHTS.values()) == pytest.approx(1.0)

    def test_evaluate_technician_readiness_full(self):
        """Full evaluation produces correct composite score."""
        tech_id = str(uuid.uuid4())
        tech = make_technician(
            tech_id=tech_id,
            career_stage="Deployed",
            deployability_status="Ready Now",
            skills=[
                make_tech_skill("Fiber Splicing", "Advanced", 350),
                make_tech_skill("OTDR Testing", "Intermediate", 150),
            ],
            certifications=[
                make_cert("FOA CFOT", "Active"),
                make_cert("OSHA 10", "Active"),
            ],
            documents=[
                make_document("Background Check", "Verified"),
                make_document("Government ID", "Verified"),
            ],
        )

        session = MagicMock()
        session.get.return_value = tech
        session.query.return_value.filter.return_value.all.return_value = []
        session.query.return_value.filter.return_value.first.return_value = None

        result = evaluate_technician_readiness(session, tech_id)

        assert result.technician_id == tech_id
        assert result.technician_name == "John Doe"
        assert 0 <= result.overall_score <= 100
        assert result.certification.score >= 0
        assert result.training.score >= 0
        assert result.assignment_history.score >= 0
        assert result.documentation.score >= 0
        assert len(result.dimension_scores) == 4

    def test_evaluate_not_found_raises(self):
        """Missing technician raises ValueError."""
        session = MagicMock()
        session.get.return_value = None

        with pytest.raises(ValueError, match="not found"):
            evaluate_technician_readiness(session, "nonexistent-id")

    def test_to_dict_serialization(self):
        """ReadinessResult.to_dict() produces a valid dict."""
        tech_id = str(uuid.uuid4())
        tech = make_technician(
            tech_id=tech_id,
            skills=[make_tech_skill("Fiber Splicing", "Intermediate", 120)],
            certifications=[make_cert("OSHA 10", "Active")],
        )

        session = MagicMock()
        session.get.return_value = tech
        session.query.return_value.filter.return_value.all.return_value = []
        session.query.return_value.filter.return_value.first.return_value = None

        result = evaluate_technician_readiness(session, tech_id)
        d = result.to_dict()

        assert "technician_id" in d
        assert "overall_score" in d
        assert "dimension_scores" in d
        assert "certification" in d
        assert "training" in d
        assert "assignment_history" in d
        assert "documentation" in d
        assert isinstance(d["overall_score"], float)


# ---------------------------------------------------------------------------
# Apply Status Update Tests
# ---------------------------------------------------------------------------

class TestApplyStatusUpdate:
    def test_apply_status_change(self):
        """Applying a recommended status change works."""
        tech_id = str(uuid.uuid4())
        tech = make_technician(
            tech_id=tech_id,
            deployability_status="In Training",
        )

        session = MagicMock()
        session.get.return_value = tech

        result = ReadinessResult(
            technician_id=tech_id,
            technician_name="John Doe",
            overall_score=75.0,
            current_status="In Training",
            suggested_status="Ready Now",
            status_change_recommended=True,
            status_change_reason="All requirements met",
        )

        update = apply_readiness_status_update(session, tech_id, result)
        assert update["changed"] is True
        assert update["new_status"] == "Ready Now"

    def test_no_change_when_not_recommended(self):
        """No change applied when not recommended."""
        tech_id = str(uuid.uuid4())
        tech = make_technician(tech_id=tech_id, deployability_status="Ready Now")

        session = MagicMock()
        session.get.return_value = tech

        result = ReadinessResult(
            technician_id=tech_id,
            technician_name="John Doe",
            current_status="Ready Now",
            suggested_status="Ready Now",
            status_change_recommended=False,
        )

        update = apply_readiness_status_update(session, tech_id, result)
        assert update["changed"] is False

    def test_apply_not_found_raises(self):
        """Applying to missing technician raises ValueError."""
        session = MagicMock()
        session.get.return_value = None

        result = ReadinessResult(
            technician_id="missing",
            technician_name="Nobody",
            status_change_recommended=True,
            suggested_status="Ready Now",
        )

        with pytest.raises(ValueError, match="not found"):
            apply_readiness_status_update(session, "missing", result)


# ---------------------------------------------------------------------------
# Batch Evaluation Tests
# ---------------------------------------------------------------------------

class TestBatchEvaluation:
    def test_batch_evaluates_all_active(self):
        """Batch evaluation processes all non-inactive technicians."""
        techs = [
            make_technician(tech_id=str(uuid.uuid4()), name=f"Tech {i}")
            for i in range(5)
        ]

        session = MagicMock()
        session.query.return_value.filter.return_value.all.side_effect = [
            techs,     # First call: query technicians
            [], [], [], [], [],  # Assignment queries for each tech
        ]
        # Make session.get return the right tech for each ID
        session.get.side_effect = lambda model, tid: next(
            (t for t in techs if str(t.id) == str(tid)), None
        )
        # Skill definition lookups
        session.query.return_value.filter.return_value.first.return_value = None

        results = evaluate_all_technicians_readiness(session, only_active=True)
        assert len(results) == 5
