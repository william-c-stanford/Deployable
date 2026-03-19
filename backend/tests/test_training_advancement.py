"""Tests for training advancement Celery tasks and WebSocket notifications.

Tests the complete flow:
1. Timesheet approval → hours accumulation
2. Hours accumulation → proficiency advancement check
3. Proficiency advancement → WebSocket notification emission
4. Career stage advancement → WebSocket notification emission
"""

import pytest
from unittest.mock import MagicMock, patch, call
from datetime import date, datetime, timezone
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
    tech_id=None,
    name="Test Tech",
    career_stage="In Training",
    skills=None,
    certifications=None,
    total_approved_hours=0,
):
    tech = MagicMock()
    tech.id = tech_id or uuid.uuid4()
    tech.full_name = name
    tech.first_name = name.split()[0]
    tech.last_name = name.split()[-1]
    tech.career_stage = MagicMock(value=career_stage)
    tech.skills = skills or []
    tech.certifications = certifications or []
    tech.total_approved_hours = total_approved_hours
    tech.deployability_locked = False
    return tech


def _make_mock_skill(
    skill_id=None,
    tech_id=None,
    skill_name="Fiber Splicing",
    proficiency_level="Beginner",
    hours=0.0,
):
    skill = MagicMock()
    skill.id = skill_id or uuid.uuid4()
    skill.technician_id = tech_id or uuid.uuid4()
    skill.skill_name = skill_name
    skill.proficiency_level = MagicMock(value=proficiency_level)
    skill.training_hours_accumulated = hours
    return skill


# ---------------------------------------------------------------------------
# Tests: process_approved_timesheet
# ---------------------------------------------------------------------------

class TestProcessApprovedTimesheet:
    """Tests for the process_approved_timesheet Celery task."""

    @patch("app.workers.tasks.training._broadcast_ws_notification")
    @patch("app.workers.tasks.training.SessionLocal")
    def test_accumulates_hours_on_specific_skill(self, mock_session_cls, mock_ws):
        """Approved timesheet adds hours to the specified skill."""
        from app.workers.tasks.training import process_approved_timesheet

        tech_id = str(uuid.uuid4())
        skill_id = str(uuid.uuid4())

        # Setup mock skill
        mock_skill = MagicMock()
        mock_skill.id = skill_id
        mock_skill.skill_name = "Fiber Splicing"
        mock_skill.training_hours_accumulated = 50.0

        # Setup mock technician
        mock_tech = _make_mock_technician(tech_id=tech_id, total_approved_hours=100)
        mock_tech.skills = [mock_skill]

        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.return_value = mock_tech
        session.query.return_value.filter.return_value.first.return_value = mock_skill

        event = _make_event_dict(
            "timesheet.approved",
            "timesheet",
            str(uuid.uuid4()),
            data={
                "technician_id": tech_id,
                "hours": 20.0,
                "skill_name": "Fiber Splicing",
            },
        )

        # Create mock task instance
        result = process_approved_timesheet.run(event)

        assert result["status"] == "ok"
        assert result["hours_accumulated"] == 20.0
        assert len(result["skills_updated"]) == 1
        assert result["skills_updated"][0]["skill_name"] == "Fiber Splicing"
        assert result["skills_updated"][0]["old_hours"] == 50.0
        assert result["skills_updated"][0]["new_hours"] == 70.0

        # Verify cascade event was created
        assert len(result["cascade_events"]) == 1
        assert result["cascade_events"][0]["event_type"] == "training.hours_logged"

        # Verify WS notification was sent
        mock_ws.assert_called_once()
        ws_call = mock_ws.call_args
        assert ws_call[0][0] == "technicians"
        assert ws_call[0][1]["event_type"] == "training.hours_approved"

    @patch("app.workers.tasks.training._broadcast_ws_notification")
    @patch("app.workers.tasks.training.SessionLocal")
    def test_distributes_hours_evenly_when_no_skill_specified(self, mock_session_cls, mock_ws):
        """When no skill_name, distribute hours evenly across all skills."""
        from app.workers.tasks.training import process_approved_timesheet

        tech_id = str(uuid.uuid4())
        skill1 = _make_mock_skill(tech_id=tech_id, skill_name="Fiber Splicing", hours=40.0)
        skill2 = _make_mock_skill(tech_id=tech_id, skill_name="OTDR Testing", hours=60.0)

        mock_tech = _make_mock_technician(tech_id=tech_id, skills=[skill1, skill2])

        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.return_value = mock_tech

        event = _make_event_dict(
            "timesheet.approved",
            "timesheet",
            str(uuid.uuid4()),
            data={"technician_id": tech_id, "hours": 20.0, "skill_name": None},
        )

        result = process_approved_timesheet.run(event)

        assert result["status"] == "ok"
        assert len(result["skills_updated"]) == 2
        # Each skill should get 10 hours (20 / 2)
        for su in result["skills_updated"]:
            assert su["new_hours"] - su["old_hours"] == pytest.approx(10.0)

    @patch("app.workers.tasks.training._broadcast_ws_notification")
    @patch("app.workers.tasks.training.SessionLocal")
    def test_updates_total_approved_hours(self, mock_session_cls, mock_ws):
        """Total approved hours on technician should be incremented."""
        from app.workers.tasks.training import process_approved_timesheet

        tech_id = str(uuid.uuid4())
        mock_tech = _make_mock_technician(tech_id=tech_id, total_approved_hours=100)
        mock_tech.skills = []

        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.return_value = mock_tech

        event = _make_event_dict(
            "timesheet.approved",
            "timesheet",
            str(uuid.uuid4()),
            data={"technician_id": tech_id, "hours": 25.0, "skill_name": None},
        )

        result = process_approved_timesheet.run(event)

        assert mock_tech.total_approved_hours == 125.0

    @patch("app.workers.tasks.training._broadcast_ws_notification")
    @patch("app.workers.tasks.training.SessionLocal")
    def test_skips_when_no_technician_id(self, mock_session_cls, mock_ws):
        """Should skip if no technician_id in event data."""
        from app.workers.tasks.training import process_approved_timesheet

        event = _make_event_dict(
            "timesheet.approved",
            "timesheet",
            str(uuid.uuid4()),
            data={"hours": 20.0},
        )

        result = process_approved_timesheet.run(event)

        assert result["status"] == "skipped"
        mock_ws.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: advance_proficiency with WebSocket notifications
# ---------------------------------------------------------------------------

class TestAdvanceProficiencyWithWS:
    """Tests that advance_proficiency emits WebSocket notifications."""

    @patch("app.workers.tasks.training._broadcast_ws_notification")
    @patch("app.workers.tasks.training.evaluate_skill_advancement")
    @patch("app.workers.tasks.training.SessionLocal")
    def test_emits_ws_notification_on_level_change(
        self, mock_session_cls, mock_eval, mock_ws
    ):
        """When proficiency level changes, WS notification should be sent."""
        from app.workers.tasks.training import advance_proficiency

        tech_id = str(uuid.uuid4())
        skill_id = str(uuid.uuid4())

        mock_skill = _make_mock_skill(
            skill_id=skill_id,
            tech_id=tech_id,
            skill_name="Fiber Splicing",
            proficiency_level="Beginner",
            hours=120.0,
        )
        mock_tech = _make_mock_technician(tech_id=tech_id, name="Jane Doe")

        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.side_effect = lambda model, id: (
            mock_skill if str(id) == skill_id else mock_tech
        )

        # Mock advancement service to allow advancement
        eval_result = MagicMock()
        eval_result.should_advance = True
        eval_result.cert_gate = None
        mock_eval.return_value = eval_result

        event = _make_event_dict(
            "training.threshold_met",
            "technician_skill",
            skill_id,
            data={
                "technician_id": tech_id,
                "skill_name": "Fiber Splicing",
                "current_level": "Beginner",
                "new_level": "Intermediate",
                "hours": 120.0,
            },
        )

        result = advance_proficiency.run(event)

        assert result["status"] == "advanced"
        assert result["ws_notification_sent"] is True

        # Should have sent WS notifications to both topics
        assert mock_ws.call_count == 2

        # First call: technicians topic
        tech_call = mock_ws.call_args_list[0]
        assert tech_call[0][0] == "technicians"
        assert tech_call[0][1]["event_type"] == "training.proficiency_advanced"
        assert tech_call[0][1]["technician_name"] == "Jane Doe"
        assert tech_call[0][1]["data"]["skill_name"] == "Fiber Splicing"
        assert tech_call[0][1]["data"]["old_level"] == "Beginner"
        assert tech_call[0][1]["data"]["new_level"] == "Intermediate"

        # Second call: dashboard topic
        dash_call = mock_ws.call_args_list[1]
        assert dash_call[0][0] == "dashboard"
        assert dash_call[0][1]["event_type"] == "dashboard.training_advancement"

    @patch("app.workers.tasks.training._broadcast_ws_notification")
    @patch("app.workers.tasks.training.evaluate_skill_advancement")
    @patch("app.workers.tasks.training.SessionLocal")
    def test_no_ws_notification_when_blocked(
        self, mock_session_cls, mock_eval, mock_ws
    ):
        """When advancement is blocked, no WS notification should be sent."""
        from app.workers.tasks.training import advance_proficiency

        tech_id = str(uuid.uuid4())
        skill_id = str(uuid.uuid4())

        mock_skill = _make_mock_skill(skill_id=skill_id, tech_id=tech_id)
        mock_tech = _make_mock_technician(tech_id=tech_id)

        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.side_effect = lambda model, id: (
            mock_skill if str(id) == skill_id else mock_tech
        )

        # Mock advancement service to block
        eval_result = MagicMock()
        eval_result.should_advance = False
        eval_result.blocked_reason = "Missing cert"
        mock_eval.return_value = eval_result

        event = _make_event_dict(
            "training.threshold_met",
            "technician_skill",
            skill_id,
            data={
                "technician_id": tech_id,
                "new_level": "Intermediate",
            },
        )

        result = advance_proficiency.run(event)

        assert result["status"] == "blocked"
        mock_ws.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: career stage advancement with WebSocket notifications
# ---------------------------------------------------------------------------

class TestCareerStageAdvancementWithWS:
    """Tests that career stage advancement emits WebSocket notifications."""

    @patch("app.workers.tasks.training._broadcast_ws_notification")
    @patch("app.workers.tasks.training.SessionLocal")
    def test_emits_ws_notification_on_career_stage_change(
        self, mock_session_cls, mock_ws
    ):
        """When career stage changes to Training Completed, WS should be sent."""
        from app.workers.tasks.training import update_career_stage_training_complete

        tech_id = str(uuid.uuid4())
        mock_tech = _make_mock_technician(
            tech_id=tech_id,
            name="John Smith",
            career_stage="In Training",
        )

        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.return_value = mock_tech

        event = _make_event_dict(
            "training.completed",
            "technician",
            tech_id,
            data={"reason": "All skills at Intermediate or above"},
        )

        result = update_career_stage_training_complete.run(event)

        assert result["status"] == "advanced"
        assert result["ws_notification_sent"] is True

        # Should broadcast to both technicians and dashboard topics
        assert mock_ws.call_count == 2

        tech_call = mock_ws.call_args_list[0]
        assert tech_call[0][0] == "technicians"
        assert tech_call[0][1]["event_type"] == "training.career_stage_advanced"
        assert "ready for assignment" in tech_call[0][1]["data"]["message"]

        dash_call = mock_ws.call_args_list[1]
        assert dash_call[0][0] == "dashboard"
        assert dash_call[0][1]["event_type"] == "dashboard.career_stage_change"

    @patch("app.workers.tasks.training._broadcast_ws_notification")
    @patch("app.workers.tasks.training.SessionLocal")
    def test_no_ws_notification_when_not_in_training(
        self, mock_session_cls, mock_ws
    ):
        """When technician is not In Training, no notification should be sent."""
        from app.workers.tasks.training import update_career_stage_training_complete

        tech_id = str(uuid.uuid4())
        mock_tech = _make_mock_technician(
            tech_id=tech_id,
            career_stage="Deployed",
        )

        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.return_value = mock_tech

        event = _make_event_dict(
            "training.completed",
            "technician",
            tech_id,
        )

        result = update_career_stage_training_complete.run(event)

        assert result["status"] == "skipped"
        mock_ws.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: WebSocket broadcast helper
# ---------------------------------------------------------------------------

class TestWSBroadcastHelper:
    """Tests for the _broadcast_ws_notification helper."""

    @patch("redis.Redis.from_url")
    def test_publishes_to_redis_channel(self, mock_redis_cls):
        """Should publish JSON message to the correct Redis channel."""
        from app.workers.tasks.training import _broadcast_ws_notification

        mock_redis = MagicMock()
        mock_redis_cls.return_value = mock_redis

        event = {
            "event_type": "training.proficiency_advanced",
            "data": {"skill_name": "Fiber Splicing"},
        }

        _broadcast_ws_notification("technicians", event)

        mock_redis.publish.assert_called_once()
        channel, message = mock_redis.publish.call_args[0]
        assert channel == "deployable:ws_broadcast"

        import json
        parsed = json.loads(message)
        assert parsed["topic"] == "technicians"
        assert parsed["event"]["event_type"] == "training.proficiency_advanced"

    @patch("redis.Redis.from_url")
    def test_handles_redis_failure_gracefully(self, mock_redis_cls):
        """Should not raise if Redis is unavailable."""
        from app.workers.tasks.training import _broadcast_ws_notification

        mock_redis_cls.side_effect = ConnectionError("Redis down")

        # Should not raise
        _broadcast_ws_notification("technicians", {"event_type": "test"})


# ---------------------------------------------------------------------------
# Tests: Event dispatcher routing
# ---------------------------------------------------------------------------

class TestTimesheetEventRouting:
    """Tests that TIMESHEET_APPROVED routes to the correct tasks."""

    def test_timesheet_approved_routes_to_process_and_check(self):
        """TIMESHEET_APPROVED should route to both process_approved_timesheet
        and check_proficiency_advancement."""
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType

        tasks = EVENT_TASK_ROUTING[EventType.TIMESHEET_APPROVED]
        assert "app.workers.tasks.training.process_approved_timesheet" in tasks
        assert "app.workers.tasks.training.check_proficiency_advancement" in tasks

    def test_training_threshold_met_routes_to_advance(self):
        """TRAINING_THRESHOLD_MET should route to advance_proficiency."""
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType

        tasks = EVENT_TASK_ROUTING[EventType.TRAINING_THRESHOLD_MET]
        assert "app.workers.tasks.training.advance_proficiency" in tasks


# ---------------------------------------------------------------------------
# Tests: End-to-end cascade chain
# ---------------------------------------------------------------------------

class TestEndToEndAdvancementCascade:
    """Tests the full event cascade: approval → hours → check → advance → WS."""

    @patch("app.workers.tasks.training._broadcast_ws_notification")
    @patch("app.workers.tasks.training.SessionLocal")
    def test_full_cascade_produces_correct_events(self, mock_session_cls, mock_ws):
        """process_approved_timesheet should produce TRAINING_HOURS_LOGGED
        cascade events that would trigger check_proficiency_advancement."""
        from app.workers.tasks.training import process_approved_timesheet

        tech_id = str(uuid.uuid4())
        skill_id = str(uuid.uuid4())

        mock_skill = MagicMock()
        mock_skill.id = skill_id
        mock_skill.skill_name = "Fiber Splicing"
        mock_skill.training_hours_accumulated = 95.0  # Near threshold

        mock_tech = _make_mock_technician(
            tech_id=tech_id,
            name="Bob Builder",
            skills=[mock_skill],
            total_approved_hours=95,
        )

        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.return_value = mock_tech
        session.query.return_value.filter.return_value.first.return_value = mock_skill

        # Approve 10 more hours → total becomes 105 (over 100 threshold)
        event = _make_event_dict(
            "timesheet.approved",
            "timesheet",
            str(uuid.uuid4()),
            data={
                "technician_id": tech_id,
                "hours": 10.0,
                "skill_name": "Fiber Splicing",
            },
        )

        result = process_approved_timesheet.run(event)

        # The cascade should include TRAINING_HOURS_LOGGED
        assert len(result["cascade_events"]) == 1
        cascade = result["cascade_events"][0]
        assert cascade["event_type"] == "training.hours_logged"
        assert cascade["data"]["technician_id"] == tech_id
        assert cascade["data"]["hours_added"] == 10.0

        # Verify skill hours were updated
        assert result["skills_updated"][0]["new_hours"] == 105.0
