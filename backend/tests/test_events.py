"""Tests for the event signal system and Celery task infrastructure."""

import uuid
from datetime import datetime, timezone

import pytest

from app.workers.events import (
    EventType,
    EventCategory,
    EventPayload,
    EVENT_CATEGORY_MAP,
)
from app.workers.dispatcher import EVENT_TASK_ROUTING


# ---------------------------------------------------------------------------
# EventType enum tests
# ---------------------------------------------------------------------------

class TestEventType:
    """Tests for EventType enum completeness and consistency."""

    def test_all_event_types_are_strings(self):
        for et in EventType:
            assert isinstance(et.value, str)

    def test_event_type_values_are_dotted(self):
        """All event type values should be category.action format."""
        for et in EventType:
            assert "." in et.value, f"{et.name} value '{et.value}' should contain a dot"

    def test_training_events_exist(self):
        training_events = [
            EventType.TRAINING_HOURS_LOGGED,
            EventType.TRAINING_THRESHOLD_MET,
            EventType.PROFICIENCY_ADVANCED,
            EventType.TRAINING_COMPLETED,
        ]
        for e in training_events:
            assert e in EventType

    def test_certification_events_exist(self):
        cert_events = [
            EventType.CERT_ADDED,
            EventType.CERT_RENEWED,
            EventType.CERT_EXPIRED,
            EventType.CERT_EXPIRING_SOON,
            EventType.CERT_REVOKED,
        ]
        for e in cert_events:
            assert e in EventType

    def test_document_events_exist(self):
        doc_events = [
            EventType.DOC_UPLOADED,
            EventType.DOC_VERIFIED,
            EventType.DOC_REJECTED,
            EventType.DOC_EXPIRED,
            EventType.ALL_DOCS_VERIFIED,
        ]
        for e in doc_events:
            assert e in EventType

    def test_assignment_events_exist(self):
        assign_events = [
            EventType.ASSIGNMENT_CREATED,
            EventType.ASSIGNMENT_STARTED,
            EventType.ASSIGNMENT_ENDED,
            EventType.ASSIGNMENT_CANCELLED,
            EventType.TECHNICIAN_ROLLING_OFF,
        ]
        for e in assign_events:
            assert e in EventType

    def test_batch_events_exist(self):
        batch_events = [
            EventType.NIGHTLY_BATCH_TRIGGERED,
            EventType.CERT_EXPIRY_SCAN_TRIGGERED,
            EventType.SCORE_REFRESH_TRIGGERED,
        ]
        for e in batch_events:
            assert e in EventType


# ---------------------------------------------------------------------------
# EventCategory mapping tests
# ---------------------------------------------------------------------------

class TestEventCategoryMap:
    """Tests for EVENT_CATEGORY_MAP completeness."""

    def test_all_event_types_mapped_to_category(self):
        """Every EventType must have a category mapping."""
        for et in EventType:
            assert et in EVENT_CATEGORY_MAP, f"{et.name} has no category mapping"

    def test_training_events_in_training_category(self):
        training_events = [
            EventType.TRAINING_HOURS_LOGGED,
            EventType.TRAINING_THRESHOLD_MET,
            EventType.PROFICIENCY_ADVANCED,
            EventType.TRAINING_COMPLETED,
        ]
        for e in training_events:
            assert EVENT_CATEGORY_MAP[e] == EventCategory.TRAINING

    def test_cert_events_in_certification_category(self):
        for e in [EventType.CERT_ADDED, EventType.CERT_EXPIRED, EventType.CERT_REVOKED]:
            assert EVENT_CATEGORY_MAP[e] == EventCategory.CERTIFICATION

    def test_doc_events_in_document_category(self):
        for e in [EventType.DOC_UPLOADED, EventType.DOC_VERIFIED, EventType.ALL_DOCS_VERIFIED]:
            assert EVENT_CATEGORY_MAP[e] == EventCategory.DOCUMENT


# ---------------------------------------------------------------------------
# EventPayload tests
# ---------------------------------------------------------------------------

class TestEventPayload:
    """Tests for EventPayload serialization and deserialization."""

    def test_create_payload(self):
        p = EventPayload(
            event_type=EventType.TRAINING_HOURS_LOGGED,
            entity_type="technician_skill",
            entity_id="skill-123",
            actor_id="user-456",
            data={"hours": 8.0},
        )
        assert p.event_type == EventType.TRAINING_HOURS_LOGGED
        assert p.entity_type == "technician_skill"
        assert p.entity_id == "skill-123"
        assert p.actor_id == "user-456"
        assert p.data == {"hours": 8.0}
        assert p.correlation_id  # auto-generated
        assert p.timestamp  # auto-generated

    def test_to_dict_roundtrip(self):
        p = EventPayload(
            event_type=EventType.CERT_ADDED,
            entity_type="technician_certification",
            entity_id="cert-789",
            actor_id="ops-user",
            data={"cert_name": "FOA CFOT"},
        )
        d = p.to_dict()
        assert d["event_type"] == "cert.added"
        assert d["entity_type"] == "technician_certification"
        assert d["entity_id"] == "cert-789"
        assert d["data"]["cert_name"] == "FOA CFOT"

        # Roundtrip
        p2 = EventPayload.from_dict(d)
        assert p2.event_type == p.event_type
        assert p2.entity_type == p.entity_type
        assert p2.entity_id == p.entity_id
        assert p2.data == p.data
        assert p2.correlation_id == p.correlation_id

    def test_from_dict_defaults(self):
        d = {
            "event_type": "doc.uploaded",
            "entity_type": "technician_document",
            "entity_id": "doc-001",
        }
        p = EventPayload.from_dict(d)
        assert p.event_type == EventType.DOC_UPLOADED
        assert p.actor_id == "system"
        assert p.data == {}
        assert p.correlation_id  # auto-generated

    def test_correlation_id_preserved(self):
        corr_id = str(uuid.uuid4())
        d = {
            "event_type": "assignment.created",
            "entity_type": "assignment",
            "entity_id": "assign-001",
            "correlation_id": corr_id,
        }
        p = EventPayload.from_dict(d)
        assert p.correlation_id == corr_id


# ---------------------------------------------------------------------------
# Dispatcher routing table tests
# ---------------------------------------------------------------------------

class TestDispatcherRouting:
    """Tests for EVENT_TASK_ROUTING completeness and consistency."""

    def test_training_hours_routes_to_correct_tasks(self):
        tasks = EVENT_TASK_ROUTING[EventType.TRAINING_HOURS_LOGGED]
        assert "app.workers.tasks.training.check_proficiency_advancement" in tasks
        assert "app.workers.tasks.training.update_deployability_for_training" in tasks

    def test_training_threshold_routes_to_advance(self):
        tasks = EVENT_TASK_ROUTING[EventType.TRAINING_THRESHOLD_MET]
        assert "app.workers.tasks.training.advance_proficiency" in tasks

    def test_cert_added_routes_to_recalc_and_refresh(self):
        tasks = EVENT_TASK_ROUTING[EventType.CERT_ADDED]
        assert "app.workers.tasks.certification.recalc_deployability_for_cert" in tasks
        assert "app.workers.tasks.recommendation.refresh_affected_recommendations" in tasks

    def test_cert_expired_routes_to_expiry_handler(self):
        tasks = EVENT_TASK_ROUTING[EventType.CERT_EXPIRED]
        assert "app.workers.tasks.certification.handle_cert_expiry" in tasks

    def test_doc_uploaded_routes_to_completeness_check(self):
        tasks = EVENT_TASK_ROUTING[EventType.DOC_UPLOADED]
        assert "app.workers.tasks.document.check_doc_completeness" in tasks

    def test_all_docs_verified_routes_to_deployability(self):
        tasks = EVENT_TASK_ROUTING[EventType.ALL_DOCS_VERIFIED]
        assert "app.workers.tasks.document.update_deployability_docs_complete" in tasks

    def test_assignment_created_has_two_handlers(self):
        tasks = EVENT_TASK_ROUTING[EventType.ASSIGNMENT_CREATED]
        assert len(tasks) == 2
        assert "app.workers.tasks.assignment.update_tech_status_for_assignment" in tasks
        assert "app.workers.tasks.assignment.notify_assignment_created" in tasks

    def test_assignment_end_triggers_recommendation_refresh(self):
        tasks = EVENT_TASK_ROUTING[EventType.ASSIGNMENT_ENDED]
        assert "app.workers.tasks.recommendation.refresh_affected_recommendations" in tasks

    def test_rolling_off_triggers_alert_and_refresh(self):
        tasks = EVENT_TASK_ROUTING[EventType.TECHNICIAN_ROLLING_OFF]
        assert "app.workers.tasks.assignment.create_rolling_off_alert" in tasks
        assert "app.workers.tasks.recommendation.refresh_affected_recommendations" in tasks

    def test_headcount_requested_generates_recommendations(self):
        tasks = EVENT_TASK_ROUTING[EventType.HEADCOUNT_REQUESTED]
        assert "app.workers.tasks.recommendation.generate_staffing_recommendations" in tasks

    def test_recommendation_rejected_triggers_feedback(self):
        tasks = EVENT_TASK_ROUTING[EventType.RECOMMENDATION_REJECTED]
        assert "app.workers.tasks.recommendation.handle_rejection_feedback" in tasks

    def test_preference_rule_changes_trigger_reeval(self):
        for event in [
            EventType.PREFERENCE_RULE_CREATED,
            EventType.PREFERENCE_RULE_UPDATED,
            EventType.PREFERENCE_RULE_DELETED,
        ]:
            tasks = EVENT_TASK_ROUTING[event]
            assert "app.workers.tasks.recommendation.reeval_recommendations_for_rule" in tasks

    def test_batch_events_routed(self):
        assert "app.workers.tasks.batch.nightly_batch" in EVENT_TASK_ROUTING[EventType.NIGHTLY_BATCH_TRIGGERED]
        assert "app.workers.tasks.batch.cert_expiry_scan" in EVENT_TASK_ROUTING[EventType.CERT_EXPIRY_SCAN_TRIGGERED]
        assert "app.workers.tasks.batch.score_refresh" in EVENT_TASK_ROUTING[EventType.SCORE_REFRESH_TRIGGERED]

    def test_all_routed_event_types_exist(self):
        """Every key in the routing table must be a valid EventType."""
        for et in EVENT_TASK_ROUTING:
            assert isinstance(et, EventType)

    def test_no_empty_task_name_strings(self):
        """No task name should be empty."""
        for et, tasks in EVENT_TASK_ROUTING.items():
            for t in tasks:
                assert t and isinstance(t, str), f"Empty task name for {et.name}"
