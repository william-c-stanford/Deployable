"""Tests for the deployability status computation engine.

Tests cover:
  - Individual rule evaluators (locked, inactive, missing critical docs,
    expired certs, missing docs, currently assigned, in training,
    rolling off, ready now, fallback)
  - Priority ordering of rules
  - Compliance summary construction
  - Full compute_deployability_status integration
  - Batch computation
  - apply_computed_status state mutation
  - Edge cases (no data, all blocked, locked + issues, etc.)
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import date, timedelta, datetime, timezone
import uuid

from app.services.deployability_engine import (
    compute_deployability_status,
    compute_all_deployability_statuses,
    apply_computed_status,
    _rule_locked,
    _rule_inactive,
    _rule_missing_critical_docs,
    _rule_expired_certs,
    _rule_missing_docs,
    _rule_currently_assigned,
    _rule_in_training,
    _rule_rolling_off,
    _rule_ready_now,
    _rule_fallback,
    _build_compliance_summary,
    DeployabilityResult,
    BlockingIssue,
    RuleResult,
    REQUIRED_DOC_TYPES,
    CRITICAL_DOC_TYPES,
    ROLLING_OFF_DAYS_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers — create mock objects matching the split model structure
# ---------------------------------------------------------------------------

def make_doc(doc_type: str, status: str = "Verified"):
    """Create a mock TechnicianDocument."""
    doc = MagicMock()
    doc.id = str(uuid.uuid4())
    doc.doc_type = doc_type
    doc.verification_status = status
    return doc


def make_cert(cert_name: str, status: str = "Active", expiry_date=None):
    """Create a mock TechnicianCertification."""
    cert = MagicMock()
    cert.id = str(uuid.uuid4())
    cert.cert_name = cert_name
    cert.status = status
    cert.expiry_date = expiry_date
    return cert


def make_assignment(status: str = "Active", start_date=None, end_date=None):
    """Create a mock Assignment."""
    a = MagicMock()
    a.id = uuid.uuid4()
    a.status = status
    a.start_date = start_date or date.today() - timedelta(days=30)
    a.end_date = end_date
    return a


def make_enrollment(status: str = "Active"):
    """Create a mock TrainingEnrollment."""
    e = MagicMock()
    e.id = str(uuid.uuid4())
    e.status = status
    return e


def make_skill(name: str, proficiency: str = "Apprentice", hours: float = 0.0):
    """Create a mock TechnicianSkill."""
    s = MagicMock()
    s.id = str(uuid.uuid4())
    s.skill_name = name
    s.proficiency_level = proficiency
    s.training_hours_accumulated = hours
    return s


def make_technician(
    deployability_status="In Training",
    career_stage="In Training",
    locked=False,
    docs=None,
    certs=None,
    enrollments=None,
    skills=None,
    docs_verified=False,
    tech_id=None,
):
    """Create a mock Technician with configurable fields."""
    tech = MagicMock()
    tech.id = tech_id or uuid.uuid4()
    tech.first_name = "Test"
    tech.last_name = "Tech"
    tech.full_name = "Test Tech"
    tech.email = "test@deployable.demo"
    tech.deployability_locked = locked
    tech.deployability_status = deployability_status
    tech.career_stage = career_stage
    tech.documents = docs or []
    tech.certifications = certs or []
    tech.training_enrollments = enrollments or []
    tech.skills = skills or []
    tech.docs_verified = docs_verified
    tech.total_approved_hours = 0
    tech.total_project_count = 0
    tech.years_experience = 0
    return tech


def make_all_docs_verified():
    """Create a full set of verified documents."""
    return [
        make_doc("background_check", "Verified"),
        make_doc("drug_screen", "Verified"),
        make_doc("drivers_license", "Verified"),
        make_doc("w9", "Verified"),
        make_doc("safety_training_record", "Verified"),
        make_doc("vehicle_insurance", "Verified"),
    ]


# ---------------------------------------------------------------------------
# Tests: Individual Rule Evaluators
# ---------------------------------------------------------------------------

class TestRuleLocked:
    def test_locked_keeps_current_status(self):
        tech = make_technician(locked=True, deployability_status="Ready Now")
        result = _rule_locked(technician=tech)
        assert result.matched is True
        assert result.status == "Ready Now"
        assert "locked" in result.reason.lower()

    def test_unlocked_does_not_match(self):
        tech = make_technician(locked=False)
        result = _rule_locked(technician=tech)
        assert result.matched is False


class TestRuleInactive:
    def test_inactive_status_matches(self):
        tech = make_technician(deployability_status="Inactive")
        result = _rule_inactive(technician=tech)
        assert result.matched is True
        assert result.status == "Inactive"

    def test_non_inactive_does_not_match(self):
        tech = make_technician(deployability_status="Ready Now")
        result = _rule_inactive(technician=tech)
        assert result.matched is False


class TestRuleMissingCriticalDocs:
    def test_missing_background_check_blocks(self):
        docs = [
            make_doc("background_check", "Not Submitted"),
            make_doc("drug_screen", "Verified"),
            make_doc("drivers_license", "Verified"),
            make_doc("w9", "Verified"),
        ]
        tech = make_technician(docs=docs)
        issues = []
        result = _rule_missing_critical_docs(technician=tech, blocking_issues=issues)
        assert result.matched is True
        assert result.status == "Missing Docs"
        assert any("background" in i.description.lower() for i in issues)

    def test_missing_drug_screen_blocks(self):
        docs = [
            make_doc("background_check", "Verified"),
            make_doc("drug_screen", "Not Submitted"),
            make_doc("drivers_license", "Verified"),
            make_doc("w9", "Verified"),
        ]
        tech = make_technician(docs=docs)
        issues = []
        result = _rule_missing_critical_docs(technician=tech, blocking_issues=issues)
        assert result.matched is True
        assert any("drug" in i.description.lower() for i in issues)

    def test_expired_drug_screen_blocks(self):
        docs = [
            make_doc("background_check", "Verified"),
            make_doc("drug_screen", "Expired"),
            make_doc("drivers_license", "Verified"),
            make_doc("w9", "Verified"),
        ]
        tech = make_technician(docs=docs)
        issues = []
        result = _rule_missing_critical_docs(technician=tech, blocking_issues=issues)
        assert result.matched is True

    def test_both_critical_docs_missing(self):
        docs = [
            make_doc("background_check", "Not Submitted"),
            make_doc("drug_screen", "Expired"),
        ]
        tech = make_technician(docs=docs)
        issues = []
        result = _rule_missing_critical_docs(technician=tech, blocking_issues=issues)
        assert result.matched is True
        assert len([i for i in issues if i.severity == "critical"]) == 2

    def test_no_docs_at_all_treats_critical_as_missing(self):
        tech = make_technician(docs=[])
        issues = []
        result = _rule_missing_critical_docs(technician=tech, blocking_issues=issues)
        assert result.matched is True
        assert "background" in result.reason.lower() or "drug" in result.reason.lower()

    def test_all_critical_verified_does_not_match(self):
        docs = [
            make_doc("background_check", "Verified"),
            make_doc("drug_screen", "Verified"),
        ]
        tech = make_technician(docs=docs)
        issues = []
        result = _rule_missing_critical_docs(technician=tech, blocking_issues=issues)
        assert result.matched is False

    def test_pending_critical_docs_do_not_block(self):
        docs = [
            make_doc("background_check", "Pending Review"),
            make_doc("drug_screen", "Pending Review"),
        ]
        tech = make_technician(docs=docs)
        issues = []
        result = _rule_missing_critical_docs(technician=tech, blocking_issues=issues)
        assert result.matched is False


class TestRuleExpiredCerts:
    def test_expired_cert_blocks(self):
        certs = [
            make_cert("FOA CFOT", "Expired", date.today() - timedelta(days=30)),
            make_cert("OSHA 30", "Active"),
        ]
        tech = make_technician(certs=certs)
        issues = []
        result = _rule_expired_certs(technician=tech, blocking_issues=issues)
        assert result.matched is True
        assert result.status == "Missing Cert"
        assert any(i.severity == "critical" for i in issues)

    def test_revoked_cert_blocks(self):
        certs = [make_cert("BICSI Tech", "Revoked")]
        tech = make_technician(certs=certs)
        issues = []
        result = _rule_expired_certs(technician=tech, blocking_issues=issues)
        assert result.matched is True
        assert result.status == "Missing Cert"

    def test_expiring_soon_cert_warns_but_does_not_block(self):
        expiry = date.today() + timedelta(days=15)
        certs = [make_cert("FOA CFOT", "Active", expiry)]
        tech = make_technician(certs=certs)
        issues = []
        result = _rule_expired_certs(technician=tech, blocking_issues=issues)
        assert result.matched is False
        assert any(i.severity == "warning" for i in issues)

    def test_active_certs_no_block(self):
        certs = [
            make_cert("FOA CFOT", "Active", date.today() + timedelta(days=365)),
            make_cert("OSHA 30", "Active"),
        ]
        tech = make_technician(certs=certs)
        issues = []
        result = _rule_expired_certs(technician=tech, blocking_issues=issues)
        assert result.matched is False

    def test_no_certs_no_block(self):
        tech = make_technician(certs=[])
        issues = []
        result = _rule_expired_certs(technician=tech, blocking_issues=issues)
        assert result.matched is False

    def test_pending_cert_no_block(self):
        certs = [make_cert("FOA CFOT", "Pending")]
        tech = make_technician(certs=certs)
        issues = []
        result = _rule_expired_certs(technician=tech, blocking_issues=issues)
        assert result.matched is False


class TestRuleMissingDocs:
    def test_missing_drivers_license_blocks(self):
        docs = [
            make_doc("background_check", "Verified"),
            make_doc("drug_screen", "Verified"),
            make_doc("drivers_license", "Not Submitted"),
            make_doc("w9", "Verified"),
        ]
        tech = make_technician(docs=docs)
        issues = []
        result = _rule_missing_docs(technician=tech, blocking_issues=issues)
        assert result.matched is True
        assert result.status == "Missing Docs"

    def test_all_required_docs_verified(self):
        docs = make_all_docs_verified()
        tech = make_technician(docs=docs)
        issues = []
        result = _rule_missing_docs(technician=tech, blocking_issues=issues)
        assert result.matched is False

    def test_no_docs_and_flag_false(self):
        tech = make_technician(docs=[], docs_verified=False)
        issues = []
        result = _rule_missing_docs(technician=tech, blocking_issues=issues)
        assert result.matched is True

    def test_no_docs_but_flag_true(self):
        tech = make_technician(docs=[], docs_verified=True)
        issues = []
        result = _rule_missing_docs(technician=tech, blocking_issues=issues)
        assert result.matched is False


class TestRuleCurrentlyAssigned:
    def test_active_assignment_matches(self):
        tech = make_technician()
        session = MagicMock()

        active = [make_assignment("Active")]
        rolling_off = []

        with patch("app.services.deployability_engine._get_active_assignments", return_value=active):
            with patch("app.services.deployability_engine._get_rolling_off_assignments", return_value=rolling_off):
                result = _rule_currently_assigned(
                    technician=tech, session=session, blocking_issues=[]
                )
        assert result.matched is True
        assert result.status == "Currently Assigned"

    def test_no_active_assignments(self):
        tech = make_technician()
        session = MagicMock()

        with patch("app.services.deployability_engine._get_active_assignments", return_value=[]):
            result = _rule_currently_assigned(
                technician=tech, session=session, blocking_issues=[]
            )
        assert result.matched is False

    def test_all_rolling_off_defers_to_rolling_off_rule(self):
        """When all active assignments are rolling off, this rule should NOT match,
        deferring to the rolling_off rule instead."""
        tech = make_technician()
        session = MagicMock()

        active = [make_assignment("Active", end_date=date.today() + timedelta(days=10))]
        rolling_off = active.copy()

        with patch("app.services.deployability_engine._get_active_assignments", return_value=active):
            with patch("app.services.deployability_engine._get_rolling_off_assignments", return_value=rolling_off):
                result = _rule_currently_assigned(
                    technician=tech, session=session, blocking_issues=[]
                )
        assert result.matched is False


class TestRuleInTraining:
    def test_sourced_career_stage_matches(self):
        tech = make_technician(career_stage="Sourced")
        result = _rule_in_training(technician=tech, blocking_issues=[])
        assert result.matched is True
        assert result.status == "In Training"

    def test_screened_career_stage_matches(self):
        tech = make_technician(career_stage="Screened")
        result = _rule_in_training(technician=tech, blocking_issues=[])
        assert result.matched is True

    def test_in_training_stage_matches(self):
        tech = make_technician(career_stage="In Training")
        result = _rule_in_training(technician=tech, blocking_issues=[])
        assert result.matched is True

    def test_deployed_stage_does_not_match(self):
        tech = make_technician(career_stage="Deployed")
        result = _rule_in_training(technician=tech, blocking_issues=[])
        assert result.matched is False

    def test_awaiting_assignment_does_not_match(self):
        tech = make_technician(career_stage="Awaiting Assignment")
        result = _rule_in_training(technician=tech, blocking_issues=[])
        assert result.matched is False


class TestRuleRollingOff:
    def test_all_assignments_ending_soon(self):
        tech = make_technician()
        session = MagicMock()

        end_date = date.today() + timedelta(days=15)
        active = [make_assignment("Active", end_date=end_date)]
        rolling_off = active.copy()

        with patch("app.services.deployability_engine._get_active_assignments", return_value=active):
            with patch("app.services.deployability_engine._get_rolling_off_assignments", return_value=rolling_off):
                result = _rule_rolling_off(
                    technician=tech, session=session, blocking_issues=[]
                )
        assert result.matched is True
        assert result.status == "Rolling Off Soon"

    def test_no_active_assignments(self):
        tech = make_technician()
        session = MagicMock()

        with patch("app.services.deployability_engine._get_active_assignments", return_value=[]):
            result = _rule_rolling_off(
                technician=tech, session=session, blocking_issues=[]
            )
        assert result.matched is False

    def test_some_not_rolling_off(self):
        tech = make_technician()
        session = MagicMock()

        active = [
            make_assignment("Active", end_date=date.today() + timedelta(days=15)),
            make_assignment("Active", end_date=date.today() + timedelta(days=90)),
        ]
        rolling_off = [active[0]]  # Only one is rolling off

        with patch("app.services.deployability_engine._get_active_assignments", return_value=active):
            with patch("app.services.deployability_engine._get_rolling_off_assignments", return_value=rolling_off):
                result = _rule_rolling_off(
                    technician=tech, session=session, blocking_issues=[]
                )
        assert result.matched is False


class TestRuleReadyNow:
    def test_training_completed_with_active_certs(self):
        certs = [make_cert("FOA CFOT", "Active")]
        tech = make_technician(
            career_stage="Training Completed",
            certs=certs,
        )
        result = _rule_ready_now(technician=tech, blocking_issues=[])
        assert result.matched is True
        assert result.status == "Ready Now"

    def test_awaiting_assignment_matches(self):
        tech = make_technician(career_stage="Awaiting Assignment")
        result = _rule_ready_now(technician=tech, blocking_issues=[])
        assert result.matched is True
        assert result.status == "Ready Now"

    def test_deployed_stage_matches(self):
        tech = make_technician(career_stage="Deployed")
        result = _rule_ready_now(technician=tech, blocking_issues=[])
        assert result.matched is True

    def test_in_training_does_not_match(self):
        tech = make_technician(career_stage="In Training")
        result = _rule_ready_now(technician=tech, blocking_issues=[])
        assert result.matched is False

    def test_sourced_does_not_match(self):
        tech = make_technician(career_stage="Sourced")
        result = _rule_ready_now(technician=tech, blocking_issues=[])
        assert result.matched is False


class TestRuleFallback:
    def test_always_matches_with_current_status(self):
        tech = make_technician(deployability_status="Missing Cert")
        result = _rule_fallback(technician=tech)
        assert result.matched is True
        assert result.status == "Missing Cert"


# ---------------------------------------------------------------------------
# Tests: Rule Priority Ordering
# ---------------------------------------------------------------------------

class TestRulePriority:
    def test_locked_overrides_expired_certs(self):
        """A locked status should prevent any rule from changing it,
        even if the technician has expired certs."""
        certs = [make_cert("FOA CFOT", "Expired")]
        docs = make_all_docs_verified()
        tech = make_technician(
            locked=True,
            deployability_status="Ready Now",
            certs=certs,
            docs=docs,
        )
        session = MagicMock()

        with patch("app.services.deployability_engine._get_active_assignments", return_value=[]):
            with patch("app.services.deployability_engine._get_rolling_off_assignments", return_value=[]):
                # Simulate full chain
                from app.services.deployability_engine import _RULE_CHAIN

                blocking_issues = []
                fired = None
                for rule_fn in _RULE_CHAIN:
                    r = rule_fn(technician=tech, session=session, blocking_issues=blocking_issues)
                    if r.matched and fired is None:
                        fired = r

        assert fired is not None
        assert fired.rule_name == "manual_lock"
        assert fired.status == "Ready Now"

    def test_missing_critical_docs_overrides_ready_now(self):
        """Missing background check should prevent Ready Now."""
        docs = [
            make_doc("background_check", "Not Submitted"),
            make_doc("drug_screen", "Verified"),
            make_doc("drivers_license", "Verified"),
            make_doc("w9", "Verified"),
        ]
        tech = make_technician(
            career_stage="Training Completed",
            deployability_status="Ready Now",
            docs=docs,
            certs=[make_cert("FOA CFOT", "Active")],
        )
        session = MagicMock()

        with patch("app.services.deployability_engine._get_active_assignments", return_value=[]):
            with patch("app.services.deployability_engine._get_rolling_off_assignments", return_value=[]):
                from app.services.deployability_engine import _RULE_CHAIN

                blocking_issues = []
                fired = None
                for rule_fn in _RULE_CHAIN:
                    r = rule_fn(technician=tech, session=session, blocking_issues=blocking_issues)
                    if r.matched and fired is None:
                        fired = r

        assert fired is not None
        assert fired.rule_name == "missing_critical_docs"
        assert fired.status == "Missing Docs"

    def test_expired_cert_overrides_currently_assigned(self):
        """Expired cert should take priority over currently assigned."""
        docs = make_all_docs_verified()
        certs = [make_cert("OSHA 30", "Expired")]
        tech = make_technician(
            career_stage="Deployed",
            docs=docs,
            certs=certs,
        )
        session = MagicMock()

        active = [make_assignment("Active")]
        with patch("app.services.deployability_engine._get_active_assignments", return_value=active):
            with patch("app.services.deployability_engine._get_rolling_off_assignments", return_value=[]):
                from app.services.deployability_engine import _RULE_CHAIN

                blocking_issues = []
                fired = None
                for rule_fn in _RULE_CHAIN:
                    r = rule_fn(technician=tech, session=session, blocking_issues=blocking_issues)
                    if r.matched and fired is None:
                        fired = r

        assert fired is not None
        assert fired.rule_name == "expired_certifications"
        assert fired.status == "Missing Cert"


# ---------------------------------------------------------------------------
# Tests: Compliance Summary
# ---------------------------------------------------------------------------

class TestComplianceSummary:
    def test_all_verified_docs(self):
        docs = make_all_docs_verified()
        tech = make_technician(docs=docs)
        session = MagicMock()

        with patch("app.services.deployability_engine._get_active_assignments", return_value=[]):
            summary = _build_compliance_summary(tech, session)

        assert summary["documents"]["all_verified"] is True
        assert summary["documents"]["background_check"] == "Verified"
        assert summary["documents"]["drug_screen"] == "Verified"

    def test_missing_docs_in_summary(self):
        docs = [
            make_doc("background_check", "Not Submitted"),
            make_doc("drug_screen", "Verified"),
        ]
        tech = make_technician(docs=docs)
        session = MagicMock()

        with patch("app.services.deployability_engine._get_active_assignments", return_value=[]):
            summary = _build_compliance_summary(tech, session)

        assert summary["documents"]["all_verified"] is False
        assert summary["documents"]["background_check"] == "Not Submitted"

    def test_cert_summary_counts(self):
        certs = [
            make_cert("FOA CFOT", "Active"),
            make_cert("OSHA 30", "Expired"),
            make_cert("BICSI Tech", "Pending"),
        ]
        tech = make_technician(certs=certs)
        session = MagicMock()

        with patch("app.services.deployability_engine._get_active_assignments", return_value=[]):
            summary = _build_compliance_summary(tech, session)

        assert summary["certifications"]["total"] == 3
        assert summary["certifications"]["active"] == 1
        assert summary["certifications"]["expired"] == 1
        assert summary["certifications"]["pending"] == 1


# ---------------------------------------------------------------------------
# Tests: Full Computation Integration
# ---------------------------------------------------------------------------

class TestComputeDeployabilityStatus:
    def test_ready_technician(self):
        """A fully compliant technician should compute as Ready Now."""
        docs = make_all_docs_verified()
        certs = [make_cert("FOA CFOT", "Active")]
        tech = make_technician(
            career_stage="Awaiting Assignment",
            deployability_status="In Training",
            docs=docs,
            certs=certs,
            docs_verified=True,
        )
        tech_id = str(tech.id)

        session = MagicMock()
        session.get.return_value = tech
        session.query.return_value.filter.return_value.all.return_value = []

        result = compute_deployability_status(session, tech_id)

        assert result.computed_status == "Ready Now"
        assert result.status_changed is True
        assert result.fired_rule.rule_name == "ready_now"
        assert len(result.blocking_issues) == 0

    def test_technician_not_found(self):
        session = MagicMock()
        session.get.return_value = None

        with pytest.raises(ValueError, match="not found"):
            compute_deployability_status(session, str(uuid.uuid4()))

    def test_blocked_by_background_check(self):
        docs = [
            make_doc("background_check", "Not Submitted"),
            make_doc("drug_screen", "Verified"),
            make_doc("drivers_license", "Verified"),
            make_doc("w9", "Verified"),
        ]
        tech = make_technician(
            career_stage="Training Completed",
            deployability_status="Ready Now",
            docs=docs,
        )
        tech_id = str(tech.id)

        session = MagicMock()
        session.get.return_value = tech
        session.query.return_value.filter.return_value.all.return_value = []

        result = compute_deployability_status(session, tech_id)

        assert result.computed_status == "Missing Docs"
        assert result.status_changed is True
        assert any(
            bi.category == "document" and bi.severity == "critical"
            for bi in result.blocking_issues
        )

    def test_to_dict_serialization(self):
        docs = make_all_docs_verified()
        tech = make_technician(
            career_stage="Awaiting Assignment",
            docs=docs,
            docs_verified=True,
        )
        tech_id = str(tech.id)

        session = MagicMock()
        session.get.return_value = tech
        session.query.return_value.filter.return_value.all.return_value = []

        result = compute_deployability_status(session, tech_id)
        d = result.to_dict()

        assert "technician_id" in d
        assert "computed_status" in d
        assert "blocking_issues" in d
        assert "compliance_summary" in d
        assert "rules_evaluated" in d
        assert isinstance(d["rules_evaluated"], list)
        assert len(d["rules_evaluated"]) > 0


# ---------------------------------------------------------------------------
# Tests: Apply Status
# ---------------------------------------------------------------------------

class TestApplyComputedStatus:
    def test_apply_status_change(self):
        tech = make_technician(deployability_status="In Training")
        tech_id = str(tech.id)

        session = MagicMock()
        session.get.return_value = tech

        dr = DeployabilityResult(
            technician_id=tech_id,
            technician_name="Test Tech",
            computed_status="Ready Now",
            current_status="In Training",
            status_changed=True,
            fired_rule=RuleResult(
                rule_name="ready_now",
                rule_priority=80,
                matched=True,
                status="Ready Now",
                reason="All requirements met",
            ),
        )

        result = apply_computed_status(session, tech_id, dr)

        assert result["changed"] is True
        assert result["old_status"] == "In Training"
        assert result["new_status"] == "Ready Now"
        session.flush.assert_called_once()

    def test_apply_no_change(self):
        tech = make_technician(deployability_status="Ready Now")
        tech_id = str(tech.id)

        session = MagicMock()
        session.get.return_value = tech

        dr = DeployabilityResult(
            technician_id=tech_id,
            technician_name="Test Tech",
            computed_status="Ready Now",
            current_status="Ready Now",
            status_changed=False,
        )

        result = apply_computed_status(session, tech_id, dr)
        assert result["changed"] is False
        session.flush.assert_not_called()

    def test_apply_tech_not_found(self):
        session = MagicMock()
        session.get.return_value = None

        dr = DeployabilityResult(
            technician_id="fake",
            technician_name="",
            computed_status="Ready Now",
            current_status="In Training",
            status_changed=True,
        )

        with pytest.raises(ValueError):
            apply_computed_status(session, "fake", dr)


# ---------------------------------------------------------------------------
# Tests: Batch Computation
# ---------------------------------------------------------------------------

class TestBatchComputation:
    @patch("app.services.deployability_engine._get_rolling_off_assignments", return_value=[])
    @patch("app.services.deployability_engine._get_active_assignments", return_value=[])
    def test_batch_evaluates_all_active(self, mock_active, mock_rolling):
        techs = [
            make_technician(
                career_stage="Awaiting Assignment",
                deployability_status="In Training",
                docs=make_all_docs_verified(),
                docs_verified=True,
                tech_id=uuid.uuid4(),
            ),
            make_technician(
                career_stage="In Training",
                deployability_status="In Training",
                docs=make_all_docs_verified(),
                docs_verified=True,
                tech_id=uuid.uuid4(),
            ),
        ]

        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = techs
        # session.get receives (ModelClass, id_string) — match on string ID
        session.get.side_effect = lambda cls, tid: next(
            (t for t in techs if str(t.id) == str(tid)), None
        )

        results = compute_all_deployability_statuses(session, only_active=True)

        assert len(results) == 2
        # First should be Ready Now (Awaiting Assignment)
        assert results[0].computed_status == "Ready Now"
        # Second should be In Training
        assert results[1].computed_status == "In Training"


# ---------------------------------------------------------------------------
# Tests: Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_no_docs_no_certs_no_enrollments(self):
        """Technician with minimal data should still compute."""
        tech = make_technician(
            career_stage="Sourced",
            docs=[],
            certs=[],
            enrollments=[],
        )
        tech_id = str(tech.id)

        session = MagicMock()
        session.get.return_value = tech
        session.query.return_value.filter.return_value.all.return_value = []

        result = compute_deployability_status(session, tech_id)

        # Should have blocking issues for missing docs
        assert len(result.blocking_issues) > 0
        # Should compute a status
        assert result.computed_status is not None

    def test_multiple_blocking_issues_accumulated(self):
        """Even when a high-priority rule fires, all rules still run
        and accumulate blocking issues."""
        docs = [
            make_doc("background_check", "Not Submitted"),
            make_doc("drug_screen", "Not Submitted"),
            make_doc("drivers_license", "Not Submitted"),
            make_doc("w9", "Not Submitted"),
        ]
        certs = [make_cert("OSHA 30", "Expired")]
        tech = make_technician(docs=docs, certs=certs)
        tech_id = str(tech.id)

        session = MagicMock()
        session.get.return_value = tech
        session.query.return_value.filter.return_value.all.return_value = []

        result = compute_deployability_status(session, tech_id)

        # Should have blocking issues from both docs and certs
        doc_issues = [bi for bi in result.blocking_issues if bi.category == "document"]
        cert_issues = [bi for bi in result.blocking_issues if bi.category == "certification"]
        assert len(doc_issues) >= 2  # At least background + drug screen
        assert len(cert_issues) >= 1  # Expired OSHA

    def test_all_rules_evaluated(self):
        """All rules should be evaluated and recorded in the audit trail."""
        docs = make_all_docs_verified()
        tech = make_technician(
            career_stage="Awaiting Assignment",
            docs=docs,
            docs_verified=True,
        )
        tech_id = str(tech.id)

        session = MagicMock()
        session.get.return_value = tech
        session.query.return_value.filter.return_value.all.return_value = []

        result = compute_deployability_status(session, tech_id)

        # Should have evaluated all rules in the chain
        rule_names = [r.rule_name for r in result.all_rules_evaluated]
        assert "manual_lock" in rule_names
        assert "inactive_status" in rule_names
        assert "missing_critical_docs" in rule_names
        assert "expired_certifications" in rule_names
        assert "missing_required_docs" in rule_names
        assert "currently_assigned" in rule_names
        assert "in_training_pipeline" in rule_names
        assert "rolling_off_soon" in rule_names
        assert "ready_now" in rule_names
        assert "fallback" in rule_names

    def test_constants_are_defined(self):
        """Required constants should be properly defined."""
        assert "background_check" in CRITICAL_DOC_TYPES
        assert "drug_screen" in CRITICAL_DOC_TYPES
        assert CRITICAL_DOC_TYPES.issubset(REQUIRED_DOC_TYPES)
        assert ROLLING_OFF_DAYS_THRESHOLD == 30
