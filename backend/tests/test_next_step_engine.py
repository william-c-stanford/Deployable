"""Tests for the next-step recommendation engine, Celery tasks, and Redis cache.

Tests cover:
  - Next-step generation for individual technicians (unit tests)
  - Ops suggested action generation (unit tests)
  - Redis caching layer (unit tests with mocked Redis)
  - Celery task orchestration (integration tests with mocked DB)
  - Persistence to PostgreSQL (integration tests)
"""

import uuid
from datetime import date, timedelta, datetime, timezone
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from app.services.next_step_engine import (
    generate_next_steps_for_technician,
    generate_all_next_steps,
    generate_ops_suggested_actions,
    persist_next_step_recommendations,
    persist_ops_suggested_actions,
    _check_expiring_certs,
    _check_expired_certs,
    _check_missing_documents,
    _check_skill_advancement,
    _check_assignment_status,
    _check_availability_update,
    PRIORITY_CRITICAL,
    PRIORITY_HIGH,
    PRIORITY_MEDIUM,
    PRIORITY_LOW,
    PRIORITY_INFO,
)
from app.services.recommendation_cache import (
    cache_next_steps,
    get_cached_next_steps,
    invalidate_next_steps,
    invalidate_all_next_steps,
    cache_suggested_actions,
    get_cached_suggested_actions,
    invalidate_suggested_actions,
    set_last_refresh_timestamp,
    get_last_refresh_timestamp,
)
from app.models.technician import (
    CareerStage,
    DeployabilityStatus,
    CertStatus,
    ProficiencyLevel,
    VerificationStatus,
)


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------

def _make_mock_technician(
    tech_id=None,
    career_stage=CareerStage.DEPLOYED,
    deployability_status=DeployabilityStatus.READY_NOW,
    certifications=None,
    documents=None,
    skills=None,
    available_from=None,
):
    """Create a mock Technician object for testing."""
    tech = MagicMock()
    tech.id = tech_id or uuid.uuid4()
    tech.first_name = "John"
    tech.last_name = "Doe"
    tech.full_name = "John Doe"
    tech.career_stage = career_stage
    tech.deployability_status = deployability_status
    tech.certifications = certifications or []
    tech.documents = documents or []
    tech.skills = skills or []
    tech.available_from = available_from
    tech.docs_verified = True
    return tech


def _make_mock_cert(cert_name, status=CertStatus.ACTIVE, expiry_date=None):
    """Create a mock TechnicianCertification."""
    cert = MagicMock()
    cert.id = uuid.uuid4()
    cert.cert_name = cert_name
    cert.status = status
    cert.expiry_date = expiry_date
    return cert


def _make_mock_doc(doc_type, verification_status=VerificationStatus.VERIFIED):
    """Create a mock TechnicianDocument."""
    doc = MagicMock()
    doc.id = uuid.uuid4()
    doc.doc_type = doc_type
    doc.verification_status = verification_status
    return doc


def _make_mock_skill(skill_name, proficiency_level=ProficiencyLevel.INTERMEDIATE, hours=50):
    """Create a mock TechnicianSkill."""
    skill = MagicMock()
    skill.id = uuid.uuid4()
    skill.skill_name = skill_name
    skill.proficiency_level = proficiency_level
    skill.training_hours_accumulated = hours
    return skill


# ===========================================================================
# Unit Tests: Individual rule evaluators
# ===========================================================================

class TestCheckExpiringCerts:
    """Test _check_expiring_certs rule evaluator."""

    def test_cert_expiring_in_7_days_is_critical(self):
        today = date(2026, 3, 19)
        cert = _make_mock_cert("FOA CFOT", expiry_date=date(2026, 3, 26))
        tech = _make_mock_technician(certifications=[cert])

        results = _check_expiring_certs(tech, today)
        assert len(results) == 1
        assert results[0]["urgency"] == "critical"
        assert results[0]["priority"] == PRIORITY_CRITICAL
        assert "FOA CFOT" in results[0]["title"]
        assert results[0]["metadata"]["days_until_expiry"] == 7

    def test_cert_expiring_in_20_days_is_high(self):
        today = date(2026, 3, 19)
        cert = _make_mock_cert("OSHA 10", expiry_date=date(2026, 4, 8))
        tech = _make_mock_technician(certifications=[cert])

        results = _check_expiring_certs(tech, today)
        assert len(results) == 1
        assert results[0]["urgency"] == "high"
        assert results[0]["priority"] == PRIORITY_HIGH

    def test_cert_expiring_in_45_days_is_medium(self):
        today = date(2026, 3, 19)
        cert = _make_mock_cert("BICSI", expiry_date=date(2026, 5, 3))
        tech = _make_mock_technician(certifications=[cert])

        results = _check_expiring_certs(tech, today)
        assert len(results) == 1
        assert results[0]["urgency"] == "medium"

    def test_cert_not_expiring_soon(self):
        today = date(2026, 3, 19)
        cert = _make_mock_cert("FOA CFOT", expiry_date=date(2027, 1, 1))
        tech = _make_mock_technician(certifications=[cert])

        results = _check_expiring_certs(tech, today)
        assert len(results) == 0

    def test_expired_cert_not_caught_by_expiring_check(self):
        today = date(2026, 3, 19)
        cert = _make_mock_cert("FOA CFOT", status=CertStatus.EXPIRED, expiry_date=date(2026, 3, 1))
        tech = _make_mock_technician(certifications=[cert])

        results = _check_expiring_certs(tech, today)
        assert len(results) == 0  # Expired certs not active

    def test_multiple_expiring_certs(self):
        today = date(2026, 3, 19)
        cert1 = _make_mock_cert("FOA CFOT", expiry_date=date(2026, 3, 25))
        cert2 = _make_mock_cert("OSHA 10", expiry_date=date(2026, 4, 15))
        tech = _make_mock_technician(certifications=[cert1, cert2])

        results = _check_expiring_certs(tech, today)
        assert len(results) == 2


class TestCheckExpiredCerts:
    """Test _check_expired_certs rule evaluator."""

    def test_expired_cert_detected(self):
        today = date(2026, 3, 19)
        cert = _make_mock_cert("FOA CFOT", status=CertStatus.EXPIRED, expiry_date=date(2026, 2, 1))
        tech = _make_mock_technician(certifications=[cert])

        results = _check_expired_certs(tech, today)
        assert len(results) == 1
        assert results[0]["priority"] == PRIORITY_CRITICAL
        assert "expired" in results[0]["title"].lower()

    def test_active_cert_not_detected_as_expired(self):
        today = date(2026, 3, 19)
        cert = _make_mock_cert("FOA CFOT", status=CertStatus.ACTIVE)
        tech = _make_mock_technician(certifications=[cert])

        results = _check_expired_certs(tech, today)
        assert len(results) == 0


class TestCheckMissingDocuments:
    """Test _check_missing_documents rule evaluator."""

    def test_not_submitted_doc_detected(self):
        doc = _make_mock_doc("Background Check", VerificationStatus.NOT_SUBMITTED)
        tech = _make_mock_technician(documents=[doc])

        results = _check_missing_documents(tech)
        assert len(results) == 1
        assert results[0]["priority"] == PRIORITY_HIGH
        assert "Background Check" in results[0]["title"]

    def test_expired_doc_detected(self):
        doc = _make_mock_doc("Drug Test", VerificationStatus.EXPIRED)
        tech = _make_mock_technician(documents=[doc])

        results = _check_missing_documents(tech)
        assert len(results) == 1
        assert results[0]["priority"] == PRIORITY_HIGH

    def test_pending_review_doc_is_info(self):
        doc = _make_mock_doc("ID Verification", VerificationStatus.PENDING_REVIEW)
        tech = _make_mock_technician(documents=[doc])

        results = _check_missing_documents(tech)
        assert len(results) == 1
        assert results[0]["priority"] == PRIORITY_INFO

    def test_verified_doc_no_action(self):
        doc = _make_mock_doc("ID Verification", VerificationStatus.VERIFIED)
        tech = _make_mock_technician(documents=[doc])

        results = _check_missing_documents(tech)
        assert len(results) == 0


class TestCheckSkillAdvancement:
    """Test _check_skill_advancement rule evaluator."""

    def test_apprentice_gets_advancement_suggestion(self):
        skill = _make_mock_skill("Fiber Splicing", ProficiencyLevel.APPRENTICE)
        tech = _make_mock_technician(skills=[skill])

        results = _check_skill_advancement(tech)
        assert len(results) == 1
        assert "Intermediate" in results[0]["title"]
        assert results[0]["priority"] == PRIORITY_LOW

    def test_intermediate_gets_advancement_suggestion(self):
        skill = _make_mock_skill("Cable Pulling", ProficiencyLevel.INTERMEDIATE)
        tech = _make_mock_technician(skills=[skill])

        results = _check_skill_advancement(tech)
        assert len(results) == 1
        assert "Advanced" in results[0]["title"]

    def test_advanced_no_advancement_suggestion(self):
        skill = _make_mock_skill("OTDR Testing", ProficiencyLevel.ADVANCED)
        tech = _make_mock_technician(skills=[skill])

        results = _check_skill_advancement(tech)
        assert len(results) == 0


class TestCheckAssignmentStatus:
    """Test _check_assignment_status rule evaluator."""

    def test_rolling_off_assignment_detected(self):
        today = date(2026, 3, 19)
        session = MagicMock()
        assignment = MagicMock()
        assignment.id = uuid.uuid4()
        assignment.end_date = date(2026, 4, 5)  # 17 days away
        assignment.status = "Active"
        session.query.return_value.filter.return_value.all.return_value = [assignment]

        tech = _make_mock_technician(
            deployability_status=DeployabilityStatus.CURRENTLY_ASSIGNED,
        )

        results = _check_assignment_status(session, tech, today)
        assert any(r["category"] == "assignment" and r["priority"] == PRIORITY_HIGH for r in results)

    def test_ready_unassigned_tech_gets_info(self):
        today = date(2026, 3, 19)
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = []  # No active assignments

        tech = _make_mock_technician(
            career_stage=CareerStage.AWAITING_ASSIGNMENT,
            deployability_status=DeployabilityStatus.READY_NOW,
        )

        results = _check_assignment_status(session, tech, today)
        assert any(r["action_key"] == "awaiting_assignment" for r in results)


class TestCheckAvailabilityUpdate:
    """Test _check_availability_update rule evaluator."""

    def test_stale_availability_date(self):
        today = date(2026, 3, 19)
        tech = _make_mock_technician(available_from=date(2025, 12, 1))

        results = _check_availability_update(tech, today)
        assert len(results) == 1
        assert "Update availability" in results[0]["title"]

    def test_recent_availability_date(self):
        today = date(2026, 3, 19)
        tech = _make_mock_technician(available_from=date(2026, 3, 10))

        results = _check_availability_update(tech, today)
        assert len(results) == 0  # Only 9 days old, under 30-day threshold


# ===========================================================================
# Integration Tests: Full next-step generation
# ===========================================================================

class TestGenerateNextStepsForTechnician:
    """Test the full generate_next_steps_for_technician function."""

    def test_technician_with_multiple_issues(self):
        today = date(2026, 3, 19)
        session = MagicMock()
        # No active training enrollments
        session.query.return_value.filter.return_value.all.return_value = []

        cert_expiring = _make_mock_cert("FOA CFOT", expiry_date=date(2026, 4, 1))
        doc_missing = _make_mock_doc("Background Check", VerificationStatus.NOT_SUBMITTED)
        skill_apprentice = _make_mock_skill("Fiber Splicing", ProficiencyLevel.APPRENTICE)

        tech = _make_mock_technician(
            career_stage=CareerStage.DEPLOYED,
            deployability_status=DeployabilityStatus.READY_NOW,
            certifications=[cert_expiring],
            documents=[doc_missing],
            skills=[skill_apprentice],
        )

        steps = generate_next_steps_for_technician(session, tech, today)

        assert len(steps) >= 3  # At least cert, doc, skill
        # Steps should be sorted by priority
        priorities = [s["priority"] for s in steps]
        assert priorities == sorted(priorities)

        # Each step should have technician context
        for step in steps:
            assert step["technician_id"] == str(tech.id)
            assert step["technician_name"] == "John Doe"
            assert "generated_at" in step

    def test_healthy_technician_gets_minimal_steps(self):
        today = date(2026, 3, 19)
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = []

        cert = _make_mock_cert("FOA CFOT", expiry_date=date(2027, 6, 1))
        doc = _make_mock_doc("Background Check", VerificationStatus.VERIFIED)
        skill = _make_mock_skill("Fiber Splicing", ProficiencyLevel.ADVANCED)

        tech = _make_mock_technician(
            career_stage=CareerStage.DEPLOYED,
            deployability_status=DeployabilityStatus.CURRENTLY_ASSIGNED,
            certifications=[cert],
            documents=[doc],
            skills=[skill],
            available_from=date(2026, 3, 15),
        )

        steps = generate_next_steps_for_technician(session, tech, today)
        # Should have very few or no urgent steps
        critical_steps = [s for s in steps if s["urgency"] == "critical"]
        assert len(critical_steps) == 0


class TestGenerateAllNextSteps:
    """Test batch generation for all technicians."""

    def test_batch_generates_stats(self):
        session = MagicMock()

        tech1 = _make_mock_technician(
            certifications=[_make_mock_cert("FOA", status=CertStatus.EXPIRED)],
        )
        tech2 = _make_mock_technician()

        # First query = all technicians, subsequent queries for enrollments/assignments
        session.query.return_value.all.return_value = [tech1, tech2]
        session.query.return_value.filter.return_value.all.return_value = []

        result = generate_all_next_steps(session)

        assert "stats" in result
        assert "results" in result
        assert result["stats"]["total_technicians"] == 2
        assert isinstance(result["results"], dict)


# ===========================================================================
# Unit Tests: Redis caching layer
# ===========================================================================

class TestRecommendationCache:
    """Test Redis caching functions with mocked Redis client."""

    @patch("app.services.recommendation_cache._get_redis")
    def test_cache_and_retrieve_next_steps(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        tech_id = str(uuid.uuid4())
        steps = [{"title": "Renew cert", "priority": 1}]

        # Cache
        result = cache_next_steps(tech_id, steps)
        assert result is True
        mock_redis.setex.assert_called_once()
        mock_redis.sadd.assert_called_once()

    @patch("app.services.recommendation_cache._get_redis")
    def test_get_cached_next_steps_hit(self, mock_get_redis):
        import json
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        tech_id = str(uuid.uuid4())
        expected = [{"title": "Renew cert", "priority": 1}]
        mock_redis.get.return_value = json.dumps(expected)

        result = get_cached_next_steps(tech_id)
        assert result == expected

    @patch("app.services.recommendation_cache._get_redis")
    def test_get_cached_next_steps_miss(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis
        mock_redis.get.return_value = None

        result = get_cached_next_steps(str(uuid.uuid4()))
        assert result is None

    @patch("app.services.recommendation_cache._get_redis")
    def test_invalidate_next_steps(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        tech_id = str(uuid.uuid4())
        result = invalidate_next_steps(tech_id)
        assert result is True
        mock_redis.delete.assert_called_once()

    @patch("app.services.recommendation_cache._get_redis")
    def test_invalidate_all_next_steps(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis
        mock_redis.smembers.return_value = {"id1", "id2", "id3"}

        count = invalidate_all_next_steps()
        assert count == 3

    @patch("app.services.recommendation_cache._get_redis")
    def test_cache_suggested_actions(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        actions = [{"action_key": "review_certs", "priority": 2}]
        result = cache_suggested_actions("ops", None, actions)
        assert result is True
        mock_redis.setex.assert_called_once()

    @patch("app.services.recommendation_cache._get_redis")
    def test_get_cached_suggested_actions(self, mock_get_redis):
        import json
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        expected = [{"action_key": "review_certs"}]
        mock_redis.get.return_value = json.dumps(expected)

        result = get_cached_suggested_actions("ops")
        assert result == expected

    @patch("app.services.recommendation_cache._get_redis")
    def test_set_and_get_refresh_timestamp(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        result = set_last_refresh_timestamp()
        assert result is True
        mock_redis.setex.assert_called_once()

    @patch("app.services.recommendation_cache._get_redis")
    def test_redis_failure_returns_none(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis
        mock_redis.get.side_effect = Exception("Redis down")

        result = get_cached_next_steps(str(uuid.uuid4()))
        assert result is None  # Graceful degradation


# ===========================================================================
# Unit Tests: Persistence
# ===========================================================================

class TestPersistNextStepRecommendations:
    """Test PostgreSQL persistence of next-step recommendations."""

    def test_persist_supersedes_existing_pending(self):
        session = MagicMock()
        existing_rec = MagicMock()
        existing_rec.status = "Pending"
        session.query.return_value.filter.return_value.all.return_value = [existing_rec]

        steps = [
            {
                "title": "Renew FOA CFOT",
                "description": "Cert expiring",
                "category": "certification",
                "urgency": "high",
                "priority": 2,
                "action_key": "renew_cert_FOA",
                "link": "/technicians/123/certs",
                "metadata": {},
            }
        ]

        tech_id = str(uuid.uuid4())
        recs = persist_next_step_recommendations(session, tech_id, steps, batch_id="batch-1")

        # Old rec should be superseded
        assert existing_rec.status == "Superseded"
        # New rec should be added
        assert session.add.call_count >= 1
        assert len(recs) == 1

    def test_persist_empty_steps(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = []

        recs = persist_next_step_recommendations(session, str(uuid.uuid4()), [])
        assert len(recs) == 0


class TestPersistOpsSuggestedActions:
    """Test PostgreSQL persistence of ops suggested actions."""

    def test_persist_replaces_old_agent_actions(self):
        session = MagicMock()
        old_action = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = [old_action]

        actions = [
            {
                "action_key": "review_expired_certs",
                "title": "3 techs with expired certs",
                "description": "Review needed",
                "link": "/technicians",
                "priority": 2,
                "category": "certification",
                "urgency": "high",
                "target_role": "ops",
            }
        ]

        result = persist_ops_suggested_actions(session, actions)
        # Old action should be deleted
        session.delete.assert_called_with(old_action)
        # New action should be added
        assert session.add.call_count >= 1
        assert len(result) == 1


# ===========================================================================
# Integration Tests: Celery tasks (mocked dependencies)
# ===========================================================================

class TestNightlyNextStepBatch:
    """Test the nightly batch Celery task."""

    @patch("app.workers.tasks.next_step.set_last_refresh_timestamp")
    @patch("app.workers.tasks.next_step.cache_suggested_actions")
    @patch("app.workers.tasks.next_step.cache_next_steps")
    @patch("app.workers.tasks.next_step.invalidate_suggested_actions")
    @patch("app.workers.tasks.next_step.invalidate_all_next_steps")
    @patch("app.workers.tasks.next_step.publish_ws_event")
    @patch("app.workers.tasks.next_step.publish_notification")
    @patch("app.workers.tasks.next_step.persist_ops_suggested_actions")
    @patch("app.workers.tasks.next_step.generate_ops_suggested_actions")
    @patch("app.workers.tasks.next_step.persist_next_step_recommendations")
    @patch("app.workers.tasks.next_step.generate_all_next_steps")
    @patch("app.workers.tasks.next_step.SessionLocal")
    def test_nightly_batch_completes(
        self,
        mock_session_local,
        mock_generate_all,
        mock_persist_next,
        mock_gen_ops,
        mock_persist_ops,
        mock_pub_notif,
        mock_pub_ws,
        mock_invalidate_all,
        mock_invalidate_actions,
        mock_cache_next,
        mock_cache_actions,
        mock_set_ts,
    ):
        mock_session = MagicMock()
        mock_session_local.return_value = mock_session

        tech_id = str(uuid.uuid4())
        mock_generate_all.return_value = {
            "stats": {
                "total_technicians": 5,
                "technicians_with_steps": 3,
                "total_steps_generated": 10,
                "by_category": {"certification": 4, "training": 6},
                "by_urgency": {"critical": 2, "high": 3, "medium": 3, "low": 2, "info": 0},
            },
            "results": {
                tech_id: [{"title": "Renew cert", "priority": 1}],
            },
        }
        mock_persist_next.return_value = [MagicMock()]
        mock_gen_ops.return_value = [{"action_key": "review_certs"}]
        mock_persist_ops.return_value = [MagicMock()]
        mock_cache_next.return_value = True

        from app.workers.tasks.next_step import nightly_next_step_batch

        # Call the task function directly (bypass Celery binding)
        result = nightly_next_step_batch(None)

        assert result["status"] == "completed"
        assert "batch_id" in result
        assert result["total_technicians"] == 5
        assert result["total_steps_generated"] == 10
        assert result["ops_actions_generated"] == 1

        # Verify cache was invalidated
        mock_invalidate_all.assert_called_once()
        mock_invalidate_actions.assert_called_once()

        # Verify cache was populated
        assert mock_cache_next.called
        assert mock_cache_actions.called

        # Verify refresh timestamp was set
        mock_set_ts.assert_called_once()

        # Verify WebSocket broadcast
        mock_pub_ws.assert_called()


class TestRefreshTechnicianNextSteps:
    """Test the event-triggered single-technician refresh task."""

    @patch("app.workers.tasks.next_step.cache_suggested_actions")
    @patch("app.workers.tasks.next_step.invalidate_suggested_actions")
    @patch("app.workers.tasks.next_step.persist_ops_suggested_actions")
    @patch("app.workers.tasks.next_step.generate_ops_suggested_actions")
    @patch("app.workers.tasks.next_step.cache_next_steps")
    @patch("app.workers.tasks.next_step.invalidate_next_steps")
    @patch("app.workers.tasks.next_step.persist_next_step_recommendations")
    @patch("app.workers.tasks.next_step.generate_next_steps_for_technician")
    @patch("app.workers.tasks.next_step.publish_ws_event")
    @patch("app.workers.tasks.next_step.SessionLocal")
    def test_refresh_single_technician(
        self,
        mock_session_local,
        mock_pub_ws,
        mock_gen_steps,
        mock_persist,
        mock_invalidate,
        mock_cache,
        mock_gen_ops,
        mock_persist_ops,
        mock_invalidate_actions,
        mock_cache_actions,
    ):
        mock_session = MagicMock()
        mock_session_local.return_value = mock_session

        tech_id = str(uuid.uuid4())
        mock_technician = MagicMock()
        mock_technician.id = tech_id
        mock_session.get.return_value = mock_technician

        steps = [
            {"title": "Renew cert", "priority": 1, "category": "cert", "urgency": "critical"},
        ]
        mock_gen_steps.return_value = steps
        mock_persist.return_value = [MagicMock()]
        mock_gen_ops.return_value = []
        mock_persist_ops.return_value = []

        from app.workers.tasks.next_step import refresh_technician_next_steps
        from app.workers.events import EventPayload, EventType

        event = EventPayload(
            event_type=EventType.CERT_EXPIRED,
            entity_type="certification",
            entity_id="cert-123",
            data={"technician_id": tech_id},
        ).to_dict()

        result = refresh_technician_next_steps(event)

        assert result["status"] == "refreshed"
        assert result["technician_id"] == tech_id
        assert result["steps_count"] == 1

        # Verify cache was updated
        mock_invalidate.assert_called_once_with(tech_id)
        mock_cache.assert_called_once_with(tech_id, steps)

        # Verify WebSocket push
        mock_pub_ws.assert_called()

    @patch("app.workers.tasks.next_step.SessionLocal")
    def test_refresh_skips_missing_technician(self, mock_session_local):
        mock_session = MagicMock()
        mock_session_local.return_value = mock_session
        mock_session.get.return_value = None

        from app.workers.tasks.next_step import refresh_technician_next_steps
        from app.workers.events import EventPayload, EventType

        event = EventPayload(
            event_type=EventType.CERT_EXPIRED,
            entity_type="technician",
            entity_id="nonexistent",
        ).to_dict()

        result = refresh_technician_next_steps(event)

        assert result["status"] == "skipped"
        assert "not found" in result["reason"]


# ===========================================================================
# Edge case tests
# ===========================================================================

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_technician_with_no_data(self):
        """Technician with no certs, docs, skills, or assignments."""
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = []

        tech = _make_mock_technician(
            career_stage=CareerStage.SOURCED,
            deployability_status=DeployabilityStatus.IN_TRAINING,
            certifications=[],
            documents=[],
            skills=[],
        )

        steps = generate_next_steps_for_technician(session, tech)
        # Should still work without errors
        assert isinstance(steps, list)

    def test_cert_expiring_on_boundary_day(self):
        """Cert expiring exactly on the 60-day boundary."""
        today = date(2026, 3, 19)
        cert = _make_mock_cert("FOA CFOT", expiry_date=date(2026, 5, 18))  # exactly 60 days
        tech = _make_mock_technician(certifications=[cert])

        results = _check_expiring_certs(tech, today)
        assert len(results) == 1
        assert results[0]["urgency"] == "medium"

    def test_cert_expiring_at_61_days_not_detected(self):
        """Cert expiring at 61 days should not be detected."""
        today = date(2026, 3, 19)
        cert = _make_mock_cert("FOA CFOT", expiry_date=date(2026, 5, 19))  # 61 days
        tech = _make_mock_technician(certifications=[cert])

        results = _check_expiring_certs(tech, today)
        assert len(results) == 0

    def test_priority_ordering(self):
        """Verify steps are ordered by priority (most urgent first)."""
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = []

        cert_expired = _make_mock_cert("CERT1", status=CertStatus.EXPIRED)
        cert_expiring = _make_mock_cert("CERT2", expiry_date=date(2026, 4, 15))
        doc_missing = _make_mock_doc("DOC", VerificationStatus.NOT_SUBMITTED)
        skill = _make_mock_skill("Fiber", ProficiencyLevel.APPRENTICE)

        tech = _make_mock_technician(
            certifications=[cert_expired, cert_expiring],
            documents=[doc_missing],
            skills=[skill],
        )

        steps = generate_next_steps_for_technician(session, tech, date(2026, 3, 19))

        priorities = [s["priority"] for s in steps]
        assert priorities == sorted(priorities), "Steps should be sorted by priority (ascending)"

    def test_cache_graceful_degradation_on_redis_failure(self):
        """Cache operations should not raise exceptions on Redis failure."""
        with patch("app.services.recommendation_cache._get_redis") as mock:
            mock.side_effect = Exception("Redis connection refused")

            # All these should return gracefully, not raise
            assert cache_next_steps("id", []) is False
            assert get_cached_next_steps("id") is None
            assert invalidate_next_steps("id") is False
            assert cache_suggested_actions("ops", None, []) is False
            assert get_cached_suggested_actions("ops") is None
