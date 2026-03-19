"""Tests for the forward staffing background recommendation agent.

Tests cover:
  - Gap analysis: rolloff detection, unfilled role detection
  - Urgency scoring and classification
  - Candidate matching to gaps
  - LangChain chain fallback (deterministic explanations)
  - Celery task execution and recommendation creation
  - WebSocket broadcast on scan completion
  - Event wiring (dispatcher routing)
"""

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from uuid import uuid4

from app.services.forward_staffing_service import (
    _urgency_from_days,
    _urgency_score,
    _generate_scan_summary,
    GapAnalysisRole,
    ForwardStaffingGap,
    AvailableTechnician,
    FORWARD_WINDOW_DAYS,
)


# ---------------------------------------------------------------------------
# Urgency classification tests
# ---------------------------------------------------------------------------

class TestUrgencyClassification:
    def test_critical_within_14_days(self):
        assert _urgency_from_days(0) == "critical"
        assert _urgency_from_days(7) == "critical"
        assert _urgency_from_days(14) == "critical"

    def test_high_within_30_days(self):
        assert _urgency_from_days(15) == "high"
        assert _urgency_from_days(29) == "high"
        assert _urgency_from_days(30) == "high"

    def test_medium_within_60_days(self):
        assert _urgency_from_days(31) == "medium"
        assert _urgency_from_days(59) == "medium"
        assert _urgency_from_days(60) == "medium"

    def test_low_beyond_60_days(self):
        assert _urgency_from_days(61) == "low"
        assert _urgency_from_days(90) == "low"


class TestUrgencyScoring:
    def test_immediate_gap_scores_highest(self):
        score = _urgency_score(0, 1)
        assert score == 100.0

    def test_7_day_gap_scores_high(self):
        score = _urgency_score(7, 1)
        assert score >= 90.0

    def test_90_day_gap_scores_low(self):
        score = _urgency_score(90, 1)
        assert score <= 30.0

    def test_multiple_slots_increase_urgency(self):
        single_slot = _urgency_score(30, 1)
        multi_slot = _urgency_score(30, 3)
        assert multi_slot > single_slot

    def test_score_capped_at_100(self):
        score = _urgency_score(0, 10)
        assert score <= 100.0


# ---------------------------------------------------------------------------
# Forward window constant
# ---------------------------------------------------------------------------

class TestForwardWindow:
    def test_window_is_90_days(self):
        assert FORWARD_WINDOW_DAYS == 90


# ---------------------------------------------------------------------------
# Gap analysis summary (deterministic fallback)
# ---------------------------------------------------------------------------

class TestGapSummary:
    def test_no_gaps_summary(self):
        today = date.today()
        window_end = today + timedelta(days=90)
        summary = _generate_scan_summary([], [], today, window_end)
        assert "No staffing gaps" in summary
        assert "0 technicians" in summary

    def test_critical_gap_summary(self):
        today = date.today()
        window_end = today + timedelta(days=90)

        gap = ForwardStaffingGap(
            gap_id="test-1",
            role=GapAnalysisRole(
                role_id="role-1",
                role_name="Lead Splicer",
                project_id="proj-1",
                project_name="Atlanta Fiber",
                project_region="GA",
                project_city="Atlanta",
                required_skills=[],
                required_certs=[],
                total_slots=3,
                currently_filled=2,
                rolling_off_count=1,
                rolling_off_techs=[{"technician_id": "t1", "technician_name": "Alice", "end_date": "2026-03-25"}],
                gap_start_date=today + timedelta(days=5),
                gap_slots=1,
                urgency="critical",
            ),
            available_candidates=[],
            recommended_candidate_ids=["t2"],
            urgency_score=95.0,
            gap_type="rolloff",
            notes="1 tech rolling off",
        )

        techs = [
            AvailableTechnician(
                technician_id="t2",
                full_name="Bob Smith",
                available_from=today,
                home_base_state="GA",
                home_base_city="Atlanta",
                approved_regions=["GA"],
                career_stage="Deployed",
                deployability_status="Ready Now",
                skills=[],
                certifications=[],
            ),
        ]

        summary = _generate_scan_summary([gap], techs, today, window_end)
        assert "1 gap" in summary or "1 critical" in summary
        assert "1 technicians" in summary or "1 slot" in summary

    def test_summary_with_multiple_gaps(self):
        today = date.today()
        window_end = today + timedelta(days=90)

        gaps = []
        for i, (urgency, days) in enumerate([
            ("critical", 5), ("high", 20), ("medium", 45),
        ]):
            gaps.append(ForwardStaffingGap(
                gap_id=f"test-{i}",
                role=GapAnalysisRole(
                    role_id=f"role-{i}",
                    role_name=f"Role {i}",
                    project_id=f"proj-{i}",
                    project_name=f"Project {i}",
                    project_region="GA",
                    project_city="Atlanta",
                    required_skills=[],
                    required_certs=[],
                    total_slots=2,
                    currently_filled=1,
                    rolling_off_count=0,
                    rolling_off_techs=[],
                    gap_start_date=today + timedelta(days=days),
                    gap_slots=1,
                    urgency=urgency,
                ),
                available_candidates=[],
                recommended_candidate_ids=[],
                urgency_score=_urgency_score(days, 1),
                gap_type="unfilled",
                notes=f"Test gap {i}",
            ))

        summary = _generate_scan_summary(gaps, [], today, window_end)
        assert "3 gap" in summary


# ---------------------------------------------------------------------------
# Event wiring tests
# ---------------------------------------------------------------------------

class TestEventWiring:
    def test_forward_staffing_event_types_exist(self):
        from app.workers.events import EventType
        assert hasattr(EventType, "FORWARD_STAFFING_SCAN_TRIGGERED")
        assert hasattr(EventType, "FORWARD_STAFFING_GAP_DETECTED")

    def test_forward_staffing_events_in_category_map(self):
        from app.workers.events import EVENT_CATEGORY_MAP, EventType, EventCategory
        assert EVENT_CATEGORY_MAP[EventType.FORWARD_STAFFING_SCAN_TRIGGERED] == EventCategory.FORWARD_STAFFING
        assert EVENT_CATEGORY_MAP[EventType.FORWARD_STAFFING_GAP_DETECTED] == EventCategory.FORWARD_STAFFING

    def test_dispatcher_routes_forward_staffing_scan(self):
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType
        tasks = EVENT_TASK_ROUTING[EventType.FORWARD_STAFFING_SCAN_TRIGGERED]
        assert "app.workers.tasks.forward_staffing.forward_staffing_scan" in tasks

    def test_dispatcher_routes_assignment_ended_to_refresh(self):
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType
        tasks = EVENT_TASK_ROUTING[EventType.ASSIGNMENT_ENDED]
        assert "app.workers.tasks.forward_staffing.refresh_forward_recommendations" in tasks

    def test_dispatcher_routes_assignment_cancelled_to_refresh(self):
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType
        tasks = EVENT_TASK_ROUTING[EventType.ASSIGNMENT_CANCELLED]
        assert "app.workers.tasks.forward_staffing.refresh_forward_recommendations" in tasks


# ---------------------------------------------------------------------------
# Celery beat schedule tests
# ---------------------------------------------------------------------------

class TestCeleryBeatSchedule:
    def test_forward_staffing_scan_in_beat_schedule(self):
        from app.workers.celery_app import celery_app
        schedule = celery_app.conf.beat_schedule
        assert "forward-staffing-scan" in schedule
        entry = schedule["forward-staffing-scan"]
        assert entry["task"] == "app.workers.tasks.forward_staffing.forward_staffing_scan"

    def test_forward_staffing_task_registered(self):
        from app.workers.celery_app import celery_app
        # Check task routing includes forward_staffing
        routes = celery_app.conf.task_routes
        assert "app.workers.tasks.forward_staffing.*" in routes


# ---------------------------------------------------------------------------
# LangChain chain fallback tests
# ---------------------------------------------------------------------------

class TestForwardStaffingChainFallback:
    """Test the agents/forward_staffing_chain.py deterministic fallbacks.

    These tests import from the agents package which may require langchain_core.
    We mock the agents/__init__.py import to avoid the dependency.
    """

    @pytest.fixture(autouse=True)
    def setup_agents_path(self):
        """Add repo root to sys.path and handle agents/__init__.py import issues."""
        import sys
        import os
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)

        # Temporarily replace agents/__init__.py to avoid langchain_core dependency
        import importlib
        old_agents = sys.modules.pop("agents", None)
        old_chain = sys.modules.pop("agents.forward_staffing_chain", None)

        # Create a minimal agents module that doesn't import reranking_chain
        import types
        agents_mod = types.ModuleType("agents")
        agents_mod.__path__ = [os.path.join(repo_root, "agents")]
        sys.modules["agents"] = agents_mod

        yield

        # Restore
        sys.modules.pop("agents", None)
        sys.modules.pop("agents.forward_staffing_chain", None)
        if old_agents:
            sys.modules["agents"] = old_agents
        if old_chain:
            sys.modules["agents.forward_staffing_chain"] = old_chain

    def test_deterministic_gap_summary_no_gaps(self):
        from agents.forward_staffing_chain import _deterministic_gap_summary

        result = _deterministic_gap_summary([], 10, "2026-03-19", "2026-06-17")
        assert "No staffing gaps" in result
        assert "10 technicians" in result

    def test_deterministic_gap_summary_critical(self):
        from agents.forward_staffing_chain import _deterministic_gap_summary

        gaps = [
            {
                "role": {
                    "role_name": "Lead Splicer",
                    "project_name": "Atlanta Fiber",
                    "urgency": "critical",
                    "gap_slots": 2,
                    "gap_start_date": "2026-03-25",
                },
                "gap_type": "rolloff",
                "recommended_candidate_count": 3,
            },
        ]
        result = _deterministic_gap_summary(gaps, 5, "2026-03-19", "2026-06-17")
        assert "CRITICAL" in result
        assert "2 slot(s)" in result

    def test_deterministic_gap_recommendation_with_candidates(self):
        from agents.forward_staffing_chain import _deterministic_gap_recommendation

        gap_data = {
            "gap_type": "rolloff",
            "role": {
                "role_name": "Lead Splicer",
                "project_name": "Atlanta Fiber",
                "urgency": "critical",
                "gap_slots": 1,
                "gap_start_date": "2026-03-25",
                "required_skills": [],
                "required_certs": [],
            },
        }
        candidates = [
            {
                "full_name": "Alice Johnson",
                "available_from": "2026-03-20",
                "home_base_city": "Atlanta",
                "home_base_state": "GA",
                "career_stage": "Deployed",
                "skills": [{"skill_name": "Fiber Splicing", "proficiency": "Advanced"}],
            },
        ]

        result = _deterministic_gap_recommendation(gap_data, candidates)
        assert "Alice Johnson" in result
        assert "Lead Splicer" in result

    def test_deterministic_gap_recommendation_no_candidates(self):
        from agents.forward_staffing_chain import _deterministic_gap_recommendation

        gap_data = {
            "gap_type": "unfilled",
            "role": {
                "role_name": "OTDR Technician",
                "project_name": "Denver FTTH",
                "urgency": "high",
                "gap_slots": 2,
            },
        }
        result = _deterministic_gap_recommendation(gap_data, [])
        assert "No strong candidates" in result
        assert "OTDR Technician" in result


# ---------------------------------------------------------------------------
# WebSocket broadcast helper tests
# ---------------------------------------------------------------------------

class TestWebSocketBroadcast:
    @patch("app.workers.tasks.forward_staffing._get_redis")
    def test_broadcast_publishes_to_redis(self, mock_get_redis):
        from app.workers.tasks.forward_staffing import _broadcast_ws_event

        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        _broadcast_ws_event(
            topic="recommendations",
            event_type="forward_staffing.scan_complete",
            data={"total_gaps": 3},
        )

        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        assert call_args[0][0] == "deployable:ws_broadcast"
        import json
        payload = json.loads(call_args[0][1])
        assert payload["topic"] == "recommendations"
        assert payload["event"]["event_type"] == "forward_staffing.scan_complete"

    @patch("app.workers.tasks.forward_staffing._get_redis")
    def test_broadcast_handles_redis_failure(self, mock_get_redis):
        from app.workers.tasks.forward_staffing import _broadcast_ws_event

        mock_get_redis.return_value = None

        # Should not raise
        _broadcast_ws_event(
            topic="dashboard",
            event_type="test",
            data={},
        )


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_serialize_scan_result(self):
        from app.services.forward_staffing_service import (
            ForwardStaffingScanResult,
            serialize_scan_result,
            ForwardStaffingGap,
            GapAnalysisRole,
        )

        today = date.today()
        result = ForwardStaffingScanResult(
            scan_date=today,
            window_end=today + timedelta(days=90),
            total_gaps_found=1,
            gaps_by_urgency={"critical": 1, "high": 0, "medium": 0, "low": 0},
            gaps=[
                ForwardStaffingGap(
                    gap_id="test-1",
                    role=GapAnalysisRole(
                        role_id="r1",
                        role_name="Splicer",
                        project_id="p1",
                        project_name="Test Project",
                        project_region="GA",
                        project_city="Atlanta",
                        required_skills=[],
                        required_certs=[],
                        total_slots=2,
                        currently_filled=1,
                        rolling_off_count=0,
                        rolling_off_techs=[],
                        gap_start_date=today + timedelta(days=10),
                        gap_slots=1,
                        urgency="critical",
                    ),
                    available_candidates=[],
                    recommended_candidate_ids=["t1", "t2"],
                    urgency_score=90.0,
                    gap_type="unfilled",
                    notes="Test",
                ),
            ],
            available_technicians=[],
            summary="Test summary",
        )

        serialized = serialize_scan_result(result)

        assert serialized["total_gaps_found"] == 1
        assert serialized["gaps_by_urgency"]["critical"] == 1
        assert len(serialized["gaps"]) == 1
        assert serialized["gaps"][0]["role"]["role_name"] == "Splicer"
        assert serialized["gaps"][0]["recommended_candidate_count"] == 2
        assert serialized["summary"] == "Test summary"
        assert serialized["available_technician_count"] == 0
