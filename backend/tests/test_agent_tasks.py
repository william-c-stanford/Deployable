"""Tests for the reactive agent Celery worker tasks.

Tests the recommendation generation logic, event processing, and
cascading event chains. Uses mocked DB sessions and models.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import date, timedelta, datetime, timezone
import uuid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event_dict(event_type_val, entity_type, entity_id, data=None):
    """Build a minimal event_dict as Celery tasks receive."""
    return {
        "event_type": event_type_val,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "actor_id": "system",
        "data": data or {},
        "correlation_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _make_mock_technician(
    tech_id=None, name="Test Tech", career_stage="Deployed",
    deployability_status="Ready Now", locked=False, skills=None,
    certifications=None, documents=None,
):
    tech = MagicMock()
    tech.id = tech_id or uuid.uuid4()
    tech.full_name = name
    tech.first_name = name.split()[0]
    tech.last_name = name.split()[-1]
    tech.career_stage = career_stage
    tech.deployability_status = deployability_status
    tech.deployability_locked = locked
    tech.available_from = date.today()
    tech.approved_regions = ["GA"]
    tech.home_base_city = "Atlanta"
    tech.skills = skills or []
    tech.certifications = certifications or []
    tech.documents = documents or []
    return tech


def _make_mock_tech_skill(skill_name, proficiency, hours=0.0):
    s = MagicMock()
    s.id = uuid.uuid4()
    s.skill_name = skill_name
    s.proficiency_level = proficiency
    s.training_hours_accumulated = hours
    return s


def _make_mock_cert(cert_name, status="Active", expiry_date=None):
    c = MagicMock()
    c.id = uuid.uuid4()
    c.cert_name = cert_name
    c.status = status
    c.expiry_date = expiry_date
    c.technician_id = uuid.uuid4()
    return c


def _make_mock_role(role_name="Lead Splicer", required_skills=None, required_certs=None):
    r = MagicMock()
    r.id = uuid.uuid4()
    r.role_name = role_name
    r.required_skills = required_skills or []
    r.required_certs = required_certs or []
    r.skill_weights = {}
    r.project_id = uuid.uuid4()
    r.quantity = 2
    r.filled = 0
    return r


def _make_mock_project(name="Test Project", status="Active"):
    p = MagicMock()
    p.id = uuid.uuid4()
    p.name = name
    p.start_date = date.today() + timedelta(days=14)
    p.location_region = "GA"
    p.location_city = "Atlanta"
    p.status = status
    return p


# ---------------------------------------------------------------------------
# Training Task Tests
# ---------------------------------------------------------------------------


class TestCheckProficiencyAdvancement:
    @patch("app.workers.tasks.training.SessionLocal")
    def test_detects_advancement(self, mock_session_cls):
        from app.workers.tasks.training import check_proficiency_advancement

        session = MagicMock()
        mock_session_cls.return_value = session

        tech = _make_mock_technician(skills=[
            _make_mock_tech_skill("Fiber Splicing", "Apprentice", 150),  # above 100 threshold
        ])
        session.get.return_value = tech

        event_dict = _make_event_dict(
            "training.hours_logged", "technician", str(tech.id),
            data={"technician_id": str(tech.id)},
        )

        # Call the underlying function directly (bypass Celery base)
        result = check_proficiency_advancement.run(event_dict)

        assert result["status"] == "ok"
        assert result["advancements_detected"] == 1
        assert len(result["cascade_events"]) == 1
        cascade = result["cascade_events"][0]
        assert cascade["event_type"] == "training.threshold_met"
        assert cascade["data"]["new_level"] == "Intermediate"

    @patch("app.workers.tasks.training.SessionLocal")
    def test_no_advancement_when_below_threshold(self, mock_session_cls):
        from app.workers.tasks.training import check_proficiency_advancement

        session = MagicMock()
        mock_session_cls.return_value = session

        tech = _make_mock_technician(skills=[
            _make_mock_tech_skill("Fiber Splicing", "Apprentice", 50),  # below 100
        ])
        session.get.return_value = tech

        event_dict = _make_event_dict(
            "training.hours_logged", "technician", str(tech.id),
            data={"technician_id": str(tech.id)},
        )

        result = check_proficiency_advancement.run(event_dict)
        assert result["advancements_detected"] == 0

    @patch("app.workers.tasks.training.SessionLocal")
    def test_skips_missing_technician(self, mock_session_cls):
        from app.workers.tasks.training import check_proficiency_advancement

        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.return_value = None

        event_dict = _make_event_dict(
            "training.hours_logged", "technician", "nonexistent",
        )

        result = check_proficiency_advancement.run(event_dict)
        assert result["status"] == "skipped"


class TestAdvanceProficiency:
    @patch("app.workers.tasks.training.SessionLocal")
    def test_advances_skill_level(self, mock_session_cls):
        from app.workers.tasks.training import advance_proficiency

        session = MagicMock()
        mock_session_cls.return_value = session

        skill = _make_mock_tech_skill("Fiber Splicing", "Apprentice", 150)
        session.get.return_value = skill

        event_dict = _make_event_dict(
            "training.threshold_met", "technician_skill", str(skill.id),
            data={
                "technician_id": "tech-1",
                "skill_name": "Fiber Splicing",
                "current_level": "Apprentice",
                "new_level": "Intermediate",
                "hours": 150,
            },
        )

        result = advance_proficiency.run(event_dict)
        assert result["status"] == "advanced"
        assert result["new_level"] == "Intermediate"
        assert len(result["cascade_events"]) == 1


# ---------------------------------------------------------------------------
# Certification Task Tests
# ---------------------------------------------------------------------------


class TestCertExpiry:
    @patch("app.workers.tasks.certification.SessionLocal")
    def test_creates_renewal_recommendation(self, mock_session_cls):
        from app.workers.tasks.certification import handle_cert_expiry

        session = MagicMock()
        mock_session_cls.return_value = session

        cert = _make_mock_cert("FOA CFOT", status="Expired")
        tech = _make_mock_technician()
        cert.technician_id = tech.id

        session.get.side_effect = lambda cls, id: cert if "Cert" in cls.__name__ else tech

        event_dict = _make_event_dict(
            "cert.expired", "certification", str(cert.id),
        )

        result = handle_cert_expiry.run(event_dict)
        assert result["status"] == "alert_created"
        assert result["cert_name"] == "FOA CFOT"
        # Should have added a Recommendation and SuggestedAction
        assert session.add.call_count >= 2


# ---------------------------------------------------------------------------
# Document Task Tests
# ---------------------------------------------------------------------------


class TestDocCompleteness:
    @patch("app.workers.tasks.document.SessionLocal")
    def test_all_docs_verified_cascades(self, mock_session_cls):
        from app.workers.tasks.document import check_doc_completeness

        session = MagicMock()
        mock_session_cls.return_value = session

        tech = _make_mock_technician()
        session.get.return_value = tech

        # Create verified docs for all required types
        docs = []
        for doc_type in ["Background Check", "Drug Test", "W-4", "I-9"]:
            d = MagicMock()
            d.doc_type = doc_type
            d.verification_status = "Verified"
            docs.append(d)

        session.query.return_value.filter.return_value.all.return_value = docs

        event_dict = _make_event_dict(
            "doc.verified", "document", "doc-1",
            data={"technician_id": str(tech.id)},
        )

        result = check_doc_completeness.run(event_dict)
        assert result["all_complete"] is True
        assert len(result["cascade_events"]) == 1
        assert result["cascade_events"][0]["event_type"] == "doc.all_verified"

    @patch("app.workers.tasks.document.SessionLocal")
    def test_missing_docs_detected(self, mock_session_cls):
        from app.workers.tasks.document import check_doc_completeness

        session = MagicMock()
        mock_session_cls.return_value = session

        tech = _make_mock_technician()
        session.get.return_value = tech

        # Only 2 of 4 required docs
        docs = []
        for doc_type in ["Background Check", "W-4"]:
            d = MagicMock()
            d.doc_type = doc_type
            d.verification_status = "Verified"
            docs.append(d)

        session.query.return_value.filter.return_value.all.return_value = docs

        event_dict = _make_event_dict(
            "doc.verified", "document", "doc-1",
            data={"technician_id": str(tech.id)},
        )

        result = check_doc_completeness.run(event_dict)
        assert result["all_complete"] is False
        assert len(result["missing"]) == 2


# ---------------------------------------------------------------------------
# Recommendation Task Tests
# ---------------------------------------------------------------------------


class TestHandleRejectionFeedback:
    @patch("app.workers.tasks.recommendation.SessionLocal")
    def test_suggests_preference_rule(self, mock_session_cls):
        from app.workers.tasks.recommendation import handle_rejection_feedback

        session = MagicMock()
        mock_session_cls.return_value = session

        rec = MagicMock()
        rec.id = uuid.uuid4()
        rec.target_entity_id = str(uuid.uuid4())
        rec.role_id = str(uuid.uuid4())
        rec.recommendation_type = "staffing"
        rec.rejection_reason = None

        tech = _make_mock_technician()
        role = _make_mock_role()

        def mock_get(cls, id):
            name = cls.__name__ if hasattr(cls, '__name__') else str(cls)
            if "Recommendation" in name:
                return rec
            if "Technician" in name:
                return tech
            if "ProjectRole" in name or "Role" in name:
                return role
            return None

        session.get.side_effect = mock_get

        event_dict = _make_event_dict(
            "recommendation.rejected", "recommendation", str(rec.id),
            data={"reason": "Too junior for this role"},
        )

        result = handle_rejection_feedback.run(event_dict)
        assert result["status"] == "feedback_processed"
        assert result["suggested_rule"] is not None
        assert "experience" in result["suggested_rule"].lower() or "rule_type" in result["suggested_rule"].lower()


# ---------------------------------------------------------------------------
# Agent LLM Tests
# ---------------------------------------------------------------------------


class TestAgentLLMFallbacks:
    def test_deterministic_staffing_explanation(self):
        from app.services.agent_llm import _deterministic_staffing_explanation

        scorecard = {
            "overall_score": 85.5,
            "dimensions": {
                "skills_match": {"score": 90, "detail": "All matched"},
                "certification_fit": {"score": 100, "detail": "All certs active"},
                "availability": {"score": 80, "detail": "Available 5d before start"},
                "location_fit": {"score": 70, "detail": "Approved region"},
                "experience": {"score": 85, "detail": "Deployed stage"},
            },
        }
        result = _deterministic_staffing_explanation(
            scorecard, "John Doe", "Lead Splicer", "Atlanta Ring"
        )
        assert "John Doe" in result
        assert "Lead Splicer" in result
        assert "85.5" in result

    def test_deterministic_rule_suggestion(self):
        from app.services.agent_llm import _deterministic_rule_suggestion

        result = _deterministic_rule_suggestion("Too junior")
        assert "experience_threshold" in result

        result = _deterministic_rule_suggestion("Wrong location")
        assert "location_restriction" in result

        result = _deterministic_rule_suggestion("Skill level too low")
        assert "skill_level_minimum" in result


# ---------------------------------------------------------------------------
# Event Dispatcher Tests
# ---------------------------------------------------------------------------


class TestEventDispatcher:
    def test_dispatch_routes_to_correct_tasks(self):
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType

        # Verify key routing entries exist
        assert "app.workers.tasks.training.check_proficiency_advancement" in EVENT_TASK_ROUTING[EventType.TRAINING_HOURS_LOGGED]
        assert "app.workers.tasks.recommendation.generate_staffing_recommendations" in EVENT_TASK_ROUTING[EventType.HEADCOUNT_REQUESTED]
        assert "app.workers.tasks.certification.handle_cert_expiry" in EVENT_TASK_ROUTING[EventType.CERT_EXPIRED]
        assert "app.workers.tasks.document.check_doc_completeness" in EVENT_TASK_ROUTING[EventType.DOC_VERIFIED]
        assert "app.workers.tasks.assignment.handle_assignment_end" in EVENT_TASK_ROUTING[EventType.ASSIGNMENT_ENDED]
        assert "app.workers.tasks.recommendation.handle_rejection_feedback" in EVENT_TASK_ROUTING[EventType.RECOMMENDATION_REJECTED]
        assert "app.workers.tasks.recommendation.reeval_recommendations_for_rule" in EVENT_TASK_ROUTING[EventType.PREFERENCE_RULE_CREATED]
        assert "app.workers.tasks.batch.nightly_batch" in EVENT_TASK_ROUTING[EventType.NIGHTLY_BATCH_TRIGGERED]

    def test_all_event_types_have_routing(self):
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType

        for event_type in EventType:
            assert event_type in EVENT_TASK_ROUTING, f"Missing routing for {event_type}"


class TestEventPayload:
    def test_round_trip_serialization(self):
        from app.workers.events import EventPayload, EventType

        payload = EventPayload(
            event_type=EventType.TRAINING_HOURS_LOGGED,
            entity_type="technician_skill",
            entity_id="skill-123",
            actor_id="user-1",
            data={"hours": 8.0, "technician_id": "tech-1"},
        )

        d = payload.to_dict()
        restored = EventPayload.from_dict(d)

        assert restored.event_type == EventType.TRAINING_HOURS_LOGGED
        assert restored.entity_id == "skill-123"
        assert restored.data["hours"] == 8.0
        assert restored.correlation_id == payload.correlation_id
