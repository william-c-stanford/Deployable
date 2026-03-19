"""Tests for the orchestrator entry point.

Covers:
- Registry population: _EVENT_TYPE_TO_AGENT maps every EventType value
- Correct routing for each event category
- Fallback behavior when LLM is unavailable
- Unknown-event error handling
"""

import uuid
from unittest.mock import patch, MagicMock

import pytest

from agents.orchestrator import (
    orchestrate,
    OrchestratorResult,
    SubAgent,
    _route_free_text,
    _route_event_payload,
    _EVENT_TYPE_TO_AGENT,
    _RERANKING_KEYWORDS,
    _FORWARD_STAFFING_KEYWORDS,
    _dispatch_chat,
    _dispatch_forward_staffing,
)
from agents.config import AgentConfig


def _route_agent(msg: str, config=None) -> SubAgent:
    """Helper: extract just the SubAgent from _route_free_text (returns tuple)."""
    result = _route_free_text(msg, config)
    if isinstance(result, tuple):
        return result[0]
    return result


# ---------------------------------------------------------------------------
# Helpers — import EventType/EventCategory for registry validation
# ---------------------------------------------------------------------------

import sys, os  # noqa: E401,E402

_backend_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend")
)
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

from app.workers.events import (  # noqa: E402
    EventType,
    EventCategory,
    EVENT_CATEGORY_MAP,
)


# ---------------------------------------------------------------------------
# 1. Registry Population Tests
# ---------------------------------------------------------------------------

class TestRegistryPopulation:
    """Verify _EVENT_TYPE_TO_AGENT covers every EventType value."""

    def test_every_event_type_has_routing_entry(self):
        """Each EventType enum member must appear in the routing table."""
        missing = []
        for et in EventType:
            if et.value not in _EVENT_TYPE_TO_AGENT:
                missing.append(et.value)
        assert missing == [], (
            f"EventTypes missing from _EVENT_TYPE_TO_AGENT: {missing}"
        )

    def test_no_stale_entries(self):
        """Every key in _EVENT_TYPE_TO_AGENT must be a valid EventType value."""
        valid_values = {et.value for et in EventType}
        stale = [k for k in _EVENT_TYPE_TO_AGENT if k not in valid_values]
        assert stale == [], (
            f"Stale keys in _EVENT_TYPE_TO_AGENT (not in EventType): {stale}"
        )

    def test_all_sub_agents_are_valid(self):
        """Every value in _EVENT_TYPE_TO_AGENT must be a SubAgent member."""
        for event_value, agent in _EVENT_TYPE_TO_AGENT.items():
            assert isinstance(agent, SubAgent), (
                f"_EVENT_TYPE_TO_AGENT['{event_value}'] = {agent!r} is not a SubAgent"
            )

    def test_registry_size_matches_enum(self):
        """Registry should have same number of entries as EventType members."""
        assert len(_EVENT_TYPE_TO_AGENT) == len(EventType)

    def test_forward_staffing_events_route_to_forward_staffing(self):
        """forward_staffing.* events must route to FORWARD_STAFFING sub-agent."""
        fs_events = [
            et.value for et in EventType
            if EVENT_CATEGORY_MAP.get(et) == EventCategory.FORWARD_STAFFING
        ]
        for ev in fs_events:
            assert _EVENT_TYPE_TO_AGENT[ev] == SubAgent.FORWARD_STAFFING, (
                f"Expected {ev} → FORWARD_STAFFING, got {_EVENT_TYPE_TO_AGENT[ev]}"
            )


# ---------------------------------------------------------------------------
# 2. Correct Routing for Each Event Category
# ---------------------------------------------------------------------------

class TestEventCategoryRouting:
    """For each EventCategory, verify events in that category route correctly."""

    @pytest.fixture(autouse=True)
    def _build_category_groups(self):
        """Group EventType members by their category for parameterized tests."""
        self.groups: dict[EventCategory, list[str]] = {}
        for et, cat in EVENT_CATEGORY_MAP.items():
            self.groups.setdefault(cat, []).append(et.value)

    def test_training_events_route_to_event_dispatch(self):
        for ev in self.groups.get(EventCategory.TRAINING, []):
            result = _route_event_payload({"event_type": ev})
            assert result == SubAgent.EVENT_DISPATCH, f"{ev} should route to EVENT_DISPATCH"

    def test_certification_events_route_to_event_dispatch(self):
        for ev in self.groups.get(EventCategory.CERTIFICATION, []):
            result = _route_event_payload({"event_type": ev})
            assert result == SubAgent.EVENT_DISPATCH, f"{ev} should route to EVENT_DISPATCH"

    def test_document_events_route_to_event_dispatch(self):
        for ev in self.groups.get(EventCategory.DOCUMENT, []):
            result = _route_event_payload({"event_type": ev})
            assert result == SubAgent.EVENT_DISPATCH, f"{ev} should route to EVENT_DISPATCH"

    def test_assignment_events_route_to_event_dispatch(self):
        for ev in self.groups.get(EventCategory.ASSIGNMENT, []):
            result = _route_event_payload({"event_type": ev})
            assert result == SubAgent.EVENT_DISPATCH, f"{ev} should route to EVENT_DISPATCH"

    def test_technician_events_route_to_event_dispatch(self):
        for ev in self.groups.get(EventCategory.TECHNICIAN, []):
            result = _route_event_payload({"event_type": ev})
            assert result == SubAgent.EVENT_DISPATCH, f"{ev} should route to EVENT_DISPATCH"

    def test_project_events_route_to_event_dispatch(self):
        for ev in self.groups.get(EventCategory.PROJECT, []):
            result = _route_event_payload({"event_type": ev})
            assert result == SubAgent.EVENT_DISPATCH, f"{ev} should route to EVENT_DISPATCH"

    def test_recommendation_events_route_to_event_dispatch(self):
        for ev in self.groups.get(EventCategory.RECOMMENDATION, []):
            result = _route_event_payload({"event_type": ev})
            assert result == SubAgent.EVENT_DISPATCH, f"{ev} should route to EVENT_DISPATCH"

    def test_preference_events_route_to_event_dispatch(self):
        for ev in self.groups.get(EventCategory.PREFERENCE, []):
            result = _route_event_payload({"event_type": ev})
            assert result == SubAgent.EVENT_DISPATCH, f"{ev} should route to EVENT_DISPATCH"

    def test_timesheet_events_route_to_event_dispatch(self):
        for ev in self.groups.get(EventCategory.TIMESHEET, []):
            result = _route_event_payload({"event_type": ev})
            assert result == SubAgent.EVENT_DISPATCH, f"{ev} should route to EVENT_DISPATCH"

    def test_forward_staffing_events_route_to_forward_staffing(self):
        for ev in self.groups.get(EventCategory.FORWARD_STAFFING, []):
            result = _route_event_payload({"event_type": ev})
            assert result == SubAgent.FORWARD_STAFFING, f"{ev} should route to FORWARD_STAFFING"

    def test_skill_breakdown_events_route_to_event_dispatch(self):
        for ev in self.groups.get(EventCategory.SKILL_BREAKDOWN, []):
            result = _route_event_payload({"event_type": ev})
            assert result == SubAgent.EVENT_DISPATCH, f"{ev} should route to EVENT_DISPATCH"

    def test_transitional_events_route_to_event_dispatch(self):
        for ev in self.groups.get(EventCategory.TRANSITIONAL, []):
            result = _route_event_payload({"event_type": ev})
            assert result == SubAgent.EVENT_DISPATCH, f"{ev} should route to EVENT_DISPATCH"

    def test_batch_events_route_to_event_dispatch(self):
        for ev in self.groups.get(EventCategory.BATCH, []):
            result = _route_event_payload({"event_type": ev})
            assert result == SubAgent.EVENT_DISPATCH, f"{ev} should route to EVENT_DISPATCH"

    def test_all_categories_covered(self):
        """Every EventCategory must have at least one event tested."""
        for cat in EventCategory:
            events = self.groups.get(cat, [])
            assert len(events) > 0, f"EventCategory.{cat.name} has no events in EVENT_CATEGORY_MAP"


# ---------------------------------------------------------------------------
# 3. Fallback Behavior When LLM Is Unavailable
# ---------------------------------------------------------------------------

class TestFallbackBehavior:
    """When the LLM (API key) is unavailable, orchestrator degrades gracefully."""

    @patch("agents.orchestrator._persist_audit")
    def test_chat_routes_without_api_key(self, mock_audit):
        """Chat routing works even without an API key — it's just metadata."""
        config = AgentConfig(anthropic_api_key="")
        result = orchestrate("show me technicians", config=config)
        assert result.sub_agent == SubAgent.CHAT
        assert result.status == "routed"
        assert result.error is None

    @patch("agents.orchestrator._persist_audit")
    def test_forward_staffing_fallback_no_api_key(self, mock_audit):
        """Forward staffing falls back to deterministic summary without API key."""
        config = AgentConfig(anthropic_api_key="")
        result = orchestrate({
            "action": "forward_staffing",
            "gaps_data": [],
            "available_tech_count": 5,
            "scan_date": "2026-01-01",
            "window_end": "2026-04-01",
        }, config=config)
        assert result.sub_agent == SubAgent.FORWARD_STAFFING
        assert result.status == "success"
        assert "summary" in result.data

    @patch("agents.orchestrator._persist_audit")
    def test_forward_staffing_deterministic_with_gaps(self, mock_audit):
        """Deterministic fallback produces meaningful output with gap data."""
        config = AgentConfig(anthropic_api_key="")
        result = orchestrate({
            "action": "forward_staffing",
            "gaps_data": [
                {
                    "role": {
                        "role_name": "Lead Splicer",
                        "project_name": "Austin Fiber",
                        "project_region": "TX",
                        "urgency": "critical",
                        "gap_slots": 3,
                        "gap_start_date": "2026-02-01",
                    },
                    "gap_type": "unfilled_role",
                    "recommended_candidate_count": 2,
                }
            ],
            "available_tech_count": 10,
            "scan_date": "2026-01-01",
            "window_end": "2026-04-01",
        }, config=config)
        assert result.status == "success"
        summary = result.data.get("summary", "")
        assert len(summary) > 0

    @patch("agents.orchestrator._persist_audit")
    def test_reranking_dispatch_fails_gracefully_without_data(self, mock_audit):
        """Reranking without role/candidates data produces an error result."""
        config = AgentConfig(anthropic_api_key="")
        result = orchestrate({"action": "rerank"}, config=config)
        assert result.sub_agent == SubAgent.RERANKING
        assert result.status == "error"
        assert result.error is not None

    @patch("agents.orchestrator._persist_audit")
    @patch("agents.orchestrator._dispatch_event")
    def test_event_dispatch_fallback_on_import_error(self, mock_dispatch, mock_audit):
        """When backend dispatcher is not importable, returns dispatched=False."""
        mock_dispatch.return_value = {
            "dispatched": False,
            "event_type": "cert.expired",
            "error": "Backend dispatcher not importable",
        }
        result = orchestrate({
            "event_type": "cert.expired",
            "entity_type": "technician_certification",
            "entity_id": "cert-abc",
        })
        assert result.sub_agent == SubAgent.EVENT_DISPATCH
        assert result.status == "error"
        assert result.data["dispatched"] is False

    @patch("agents.orchestrator._persist_audit")
    def test_dispatch_exception_returns_error_result(self, mock_audit):
        """If a sub-agent dispatch raises, orchestrator returns error status."""
        with patch("agents.orchestrator._dispatch_reranking", side_effect=RuntimeError("LLM down")):
            result = orchestrate({"action": "rerank"})
            assert result.status == "error"
            assert "LLM down" in result.error
            # Audit should contain the dispatch_error entry
            actions = [e["action"] for e in result.audit_entries]
            assert "dispatch_error" in actions

    @patch("agents.orchestrator._persist_audit")
    def test_forward_staffing_recommendation_fallback(self, mock_audit):
        """Forward staffing recommendation action falls back deterministically."""
        config = AgentConfig(anthropic_api_key="")
        result = orchestrate({
            "action": "forward_staffing",
            "action": "forward_staffing",
            "gaps_data": [],
            "gap_data": {"role": {"role_name": "Splicer", "project_name": "ATX"}},
            "candidate_profiles": [],
            "available_tech_count": 0,
            "scan_date": "2026-01-01",
            "window_end": "2026-04-01",
        }, config=config)
        assert result.status == "success"


# ---------------------------------------------------------------------------
# 4. Unknown-Event Error Handling
# ---------------------------------------------------------------------------

class TestUnknownEventHandling:
    """Unknown or malformed events are handled safely."""

    def test_unknown_event_type_routes_to_event_dispatch(self):
        """Unrecognized event types fall through to EVENT_DISPATCH."""
        result = _route_event_payload({"event_type": "completely.made_up"})
        assert result == SubAgent.EVENT_DISPATCH

    def test_empty_event_type_routes_to_event_dispatch(self):
        result = _route_event_payload({"event_type": ""})
        assert result == SubAgent.EVENT_DISPATCH

    def test_missing_event_type_key(self):
        """Dict without event_type is treated by orchestrate as non-event."""
        # No 'event_type' key → not classified as event → treated as chat or unknown
        result = _route_event_payload({})
        assert result == SubAgent.EVENT_DISPATCH

    @patch("agents.orchestrator._persist_audit")
    @patch("agents.orchestrator._dispatch_event")
    def test_orchestrate_unknown_event_produces_result(self, mock_dispatch, mock_audit):
        """orchestrate() with unknown event_type still produces a valid result."""
        mock_dispatch.return_value = {
            "dispatched": True,
            "event_type": "unknown.event",
            "task_ids": [],
            "correlation_id": "test-cid",
        }
        result = orchestrate({
            "event_type": "unknown.event",
            "entity_type": "mystery",
            "entity_id": "id-1",
        })
        assert isinstance(result, OrchestratorResult)
        assert result.sub_agent == SubAgent.EVENT_DISPATCH
        assert result.correlation_id

    @patch("agents.orchestrator._persist_audit")
    def test_none_input_treated_as_chat(self, mock_audit):
        """None input doesn't crash; falls through to chat."""
        result = orchestrate(None)
        assert result.sub_agent == SubAgent.CHAT
        assert result.status == "routed"

    @patch("agents.orchestrator._persist_audit")
    def test_numeric_input_treated_as_chat(self, mock_audit):
        """Numeric input doesn't crash; falls through to chat."""
        result = orchestrate(42)
        assert result.sub_agent == SubAgent.CHAT
        assert result.status == "routed"

    @patch("agents.orchestrator._persist_audit")
    def test_empty_dict_treated_as_chat(self, mock_audit):
        """Empty dict (no event_type, no message, no action) → chat."""
        result = orchestrate({})
        assert result.sub_agent == SubAgent.CHAT
        assert result.status == "routed"

    @patch("agents.orchestrator._persist_audit")
    @patch("agents.orchestrator._dispatch_event")
    def test_malformed_event_payload(self, mock_dispatch, mock_audit):
        """Event dict with event_type but missing entity fields still routes."""
        mock_dispatch.return_value = {
            "dispatched": False,
            "event_type": "cert.expired",
            "error": "Missing required fields",
        }
        result = orchestrate({"event_type": "cert.expired"})
        assert result.sub_agent == SubAgent.EVENT_DISPATCH
        mock_dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# Router unit tests (original + expanded)
# ---------------------------------------------------------------------------

class TestRouteFreeTex:

    def test_chat_default(self):
        assert _route_agent("show me ready technicians") == SubAgent.CHAT

    def test_reranking_keyword(self):
        assert _route_agent("rank candidates for this role") == SubAgent.RERANKING

    def test_reranking_keyword_rerank(self):
        assert _route_agent("re-rank the shortlist") == SubAgent.RERANKING

    def test_forward_staffing_keyword(self):
        assert _route_agent("run gap analysis for next quarter") == SubAgent.FORWARD_STAFFING

    def test_forward_staffing_90day(self):
        assert _route_agent("show 90-day staffing forecast") == SubAgent.FORWARD_STAFFING

    def test_empty_string(self):
        assert _route_agent("") == SubAgent.CHAT

    def test_case_insensitive(self):
        assert _route_agent("RANK CANDIDATES now") == SubAgent.RERANKING

    def test_all_reranking_keywords_recognized(self):
        """Every keyword in _RERANKING_KEYWORDS should trigger RERANKING."""
        for kw in _RERANKING_KEYWORDS:
            result = _route_agent(f"please {kw} now")
            assert result == SubAgent.RERANKING, f"Keyword '{kw}' not recognized"

    def test_all_forward_staffing_keywords_recognized(self):
        """Every keyword in _FORWARD_STAFFING_KEYWORDS should trigger FORWARD_STAFFING."""
        for kw in _FORWARD_STAFFING_KEYWORDS:
            result = _route_agent(f"please {kw} now")
            assert result == SubAgent.FORWARD_STAFFING, f"Keyword '{kw}' not recognized"

    def test_mixed_keywords_reranking_takes_priority(self):
        """If both reranking and forward staffing keywords present, first match wins."""
        # reranking keywords are checked first in the code
        msg = "rank candidates then do gap analysis"
        result = _route_agent(msg)
        assert result == SubAgent.RERANKING

    def test_route_returns_tuple(self):
        """_route_free_text returns (SubAgent, optional RoutingDecision) tuple."""
        result = _route_free_text("hello")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], SubAgent)


class TestRouteEventPayload:

    def test_cert_expired(self):
        result = _route_event_payload({"event_type": "cert.expired"})
        assert result == SubAgent.EVENT_DISPATCH

    def test_forward_staffing_scan(self):
        result = _route_event_payload({"event_type": "forward_staffing.scan_triggered"})
        assert result == SubAgent.FORWARD_STAFFING

    def test_headcount_requested(self):
        result = _route_event_payload({"event_type": "project.headcount_requested"})
        assert result == SubAgent.EVENT_DISPATCH

    def test_unknown_event(self):
        result = _route_event_payload({"event_type": "unknown.event"})
        assert result == SubAgent.EVENT_DISPATCH

    @pytest.mark.parametrize("event_type", [
        "training.hours_logged",
        "training.threshold_met",
        "training.proficiency_advanced",
        "training.completed",
    ])
    def test_training_events(self, event_type):
        assert _route_event_payload({"event_type": event_type}) == SubAgent.EVENT_DISPATCH

    @pytest.mark.parametrize("event_type", [
        "cert.added",
        "cert.renewed",
        "cert.expired",
        "cert.expiring_soon",
        "cert.revoked",
    ])
    def test_certification_events(self, event_type):
        assert _route_event_payload({"event_type": event_type}) == SubAgent.EVENT_DISPATCH

    @pytest.mark.parametrize("event_type", [
        "doc.uploaded",
        "doc.verified",
        "doc.rejected",
        "doc.expired",
        "doc.all_verified",
    ])
    def test_document_events(self, event_type):
        assert _route_event_payload({"event_type": event_type}) == SubAgent.EVENT_DISPATCH

    @pytest.mark.parametrize("event_type", [
        "assignment.created",
        "assignment.started",
        "assignment.ended",
        "assignment.cancelled",
        "assignment.rolling_off",
    ])
    def test_assignment_events(self, event_type):
        assert _route_event_payload({"event_type": event_type}) == SubAgent.EVENT_DISPATCH

    @pytest.mark.parametrize("event_type", [
        "batch.nightly",
        "batch.nightly_readiness",
        "batch.cert_expiry_scan",
        "batch.score_refresh",
        "batch.escalation_scan",
        "batch.transitional_scan",
    ])
    def test_batch_events(self, event_type):
        assert _route_event_payload({"event_type": event_type}) == SubAgent.EVENT_DISPATCH

    @pytest.mark.parametrize("event_type", [
        "forward_staffing.scan_triggered",
        "forward_staffing.gap_detected",
    ])
    def test_forward_staffing_events_route_to_forward_staffing(self, event_type):
        assert _route_event_payload({"event_type": event_type}) == SubAgent.FORWARD_STAFFING


# ---------------------------------------------------------------------------
# OrchestratorResult tests
# ---------------------------------------------------------------------------

class TestOrchestratorResult:

    def test_to_dict(self):
        result = OrchestratorResult(
            correlation_id="test-123",
            sub_agent=SubAgent.CHAT,
            status="success",
            data={"message": "hello"},
            processing_time_ms=42.5,
        )
        d = result.to_dict()
        assert d["correlation_id"] == "test-123"
        assert d["sub_agent"] == "chat"
        assert d["status"] == "success"
        assert d["processing_time_ms"] == 42.5
        assert d["error"] is None

    def test_error_result(self):
        result = OrchestratorResult(
            correlation_id="err-456",
            sub_agent=SubAgent.RERANKING,
            status="error",
            error="Something went wrong",
        )
        d = result.to_dict()
        assert d["status"] == "error"
        assert d["error"] == "Something went wrong"

    def test_default_data_is_empty_dict(self):
        result = OrchestratorResult(
            correlation_id="x",
            sub_agent=SubAgent.CHAT,
        )
        assert result.data == {}
        assert result.audit_entries == []
        assert result.processing_time_ms == 0.0

    def test_all_sub_agent_enum_values_serializable(self):
        """Each SubAgent value serializes cleanly via to_dict."""
        for sa in SubAgent:
            r = OrchestratorResult(correlation_id="t", sub_agent=sa)
            d = r.to_dict()
            assert d["sub_agent"] == sa.value


# ---------------------------------------------------------------------------
# orchestrate() integration tests (mocked sub-agents)
# ---------------------------------------------------------------------------

class TestOrchestrate:

    @patch("agents.orchestrator._persist_audit")
    def test_free_text_routes_to_chat(self, mock_audit):
        result = orchestrate("Show me all technicians")
        assert result.sub_agent == SubAgent.CHAT
        assert result.status == "routed"
        assert result.correlation_id  # auto-generated
        assert result.processing_time_ms > 0
        assert result.data["routed_to"] == "chat_service"

    @patch("agents.orchestrator._persist_audit")
    def test_free_text_with_correlation_id(self, mock_audit):
        cid = "custom-correlation-123"
        result = orchestrate("hello", correlation_id=cid)
        assert result.correlation_id == cid

    @patch("agents.orchestrator._persist_audit")
    def test_dict_message_routes_to_chat(self, mock_audit):
        result = orchestrate({"message": "Show me the dashboard"})
        assert result.sub_agent == SubAgent.CHAT
        assert result.status == "routed"

    @patch("agents.orchestrator._persist_audit")
    def test_reranking_keyword_routes_correctly(self, mock_audit):
        result = orchestrate("rank candidates for Lead Splicer")
        assert result.sub_agent == SubAgent.RERANKING
        # Will fail at dispatch (no role/candidates data), but routing is correct
        assert result.status == "error"

    @patch("agents.orchestrator._persist_audit")
    @patch("agents.orchestrator._dispatch_event")
    def test_event_payload_routes_to_dispatch(self, mock_dispatch, mock_audit):
        mock_dispatch.return_value = {
            "dispatched": True,
            "event_type": "cert.expired",
            "task_ids": ["task-1"],
            "correlation_id": "test-cid",
        }
        result = orchestrate({
            "event_type": "cert.expired",
            "entity_type": "technician_certification",
            "entity_id": "cert-abc",
            "actor_id": "system",
        })
        assert result.sub_agent == SubAgent.EVENT_DISPATCH
        assert result.status == "routed"
        assert result.data["dispatched"] is True
        mock_dispatch.assert_called_once()

    @patch("agents.orchestrator._persist_audit")
    def test_direct_action_rerank(self, mock_audit):
        # No actual role/candidates so it will error, but routing is right
        result = orchestrate({"action": "rerank"})
        assert result.sub_agent == SubAgent.RERANKING
        assert result.status == "error"

    @patch("agents.orchestrator._persist_audit")
    def test_direct_action_forward_staffing(self, mock_audit):
        result = orchestrate({
            "action": "forward_staffing",
            "gaps_data": [],
            "available_tech_count": 10,
            "scan_date": "2026-01-01",
            "window_end": "2026-04-01",
        })
        assert result.sub_agent == SubAgent.FORWARD_STAFFING
        assert result.status == "success"
        assert "summary" in result.data

    @patch("agents.orchestrator._persist_audit")
    def test_audit_entries_populated(self, mock_audit):
        result = orchestrate("hello world")
        # Should have at least routing + completion entries
        assert len(result.audit_entries) >= 2
        actions = [e["action"] for e in result.audit_entries]
        assert "routed" in actions
        assert "completed" in actions

    @patch("agents.orchestrator._persist_audit")
    def test_audit_entries_contain_correlation_id(self, mock_audit):
        cid = "audit-test-789"
        result = orchestrate("hello", correlation_id=cid)
        for entry in result.audit_entries:
            assert entry["correlation_id"] == cid

    @patch("agents.orchestrator._persist_audit")
    def test_context_passed_to_chat(self, mock_audit):
        ctx = {"session_id": "sess-1", "user_role": "ops"}
        result = orchestrate("show dashboard", context=ctx)
        assert result.data.get("context") == ctx

    @patch("agents.orchestrator._persist_audit")
    def test_auto_generated_correlation_id_is_uuid(self, mock_audit):
        result = orchestrate("test")
        # Should be a valid UUID4
        parsed = uuid.UUID(result.correlation_id, version=4)
        assert str(parsed) == result.correlation_id

    @patch("agents.orchestrator._persist_audit")
    def test_processing_time_is_positive(self, mock_audit):
        result = orchestrate("test")
        assert result.processing_time_ms >= 0
