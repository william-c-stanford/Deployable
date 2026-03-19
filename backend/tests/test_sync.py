"""Tests for multi-user state sync: optimistic updates, conflict resolution,
and actor-attributed toast notifications.

Covers:
- Entity version tracking (increment, get)
- Sync event broadcast with actor attribution
- Conflict detection when concurrent modifications occur
- Actor extraction from request headers and JWT
- Sync API endpoints (status, version, broadcast)
- Entity-type-to-topic mapping
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from app.websocket import (
    ConnectionManager,
    get_entity_version,
    increment_entity_version,
    broadcast_sync_event,
    build_actor_from_request,
    _entity_type_to_topic,
    _entity_versions,
    _entity_versions_lock,
)


def run_async(coro):
    """Helper to run async tests synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Mock WebSocket
# ---------------------------------------------------------------------------

class MockWebSocket:
    """Simple mock for WebSocket testing."""

    def __init__(self, query_params=None):
        self.accepted = False
        self.sent_messages = []

    @property
    def client_state(self):
        from starlette.websockets import WebSocketState
        return WebSocketState.CONNECTED

    @property
    def query_params(self):
        return {}

    async def accept(self):
        self.accepted = True

    async def send_text(self, data: str):
        self.sent_messages.append(json.loads(data))

    async def receive_text(self):
        await asyncio.sleep(100)
        return "ping"


# ---------------------------------------------------------------------------
# Version Tracking Tests
# ---------------------------------------------------------------------------

class TestEntityVersionTracking:

    def setup_method(self):
        """Clear version state between tests."""
        _entity_versions.clear()

    def test_initial_version_is_zero(self):
        async def _test():
            v = await get_entity_version("technician", "tech-1")
            assert v == 0
        run_async(_test())

    def test_increment_version(self):
        async def _test():
            v1 = await increment_entity_version("technician", "tech-1")
            assert v1 == 1
            v2 = await increment_entity_version("technician", "tech-1")
            assert v2 == 2
            v3 = await increment_entity_version("technician", "tech-1")
            assert v3 == 3
        run_async(_test())

    def test_independent_entity_versions(self):
        async def _test():
            await increment_entity_version("technician", "tech-1")
            await increment_entity_version("technician", "tech-1")
            await increment_entity_version("recommendation", "rec-1")

            v_tech = await get_entity_version("technician", "tech-1")
            v_rec = await get_entity_version("recommendation", "rec-1")
            assert v_tech == 2
            assert v_rec == 1
        run_async(_test())

    def test_version_persists_across_reads(self):
        async def _test():
            await increment_entity_version("project", "proj-1")
            await increment_entity_version("project", "proj-1")
            v = await get_entity_version("project", "proj-1")
            assert v == 2
            # Read again — same value
            v2 = await get_entity_version("project", "proj-1")
            assert v2 == 2
        run_async(_test())


# ---------------------------------------------------------------------------
# Sync Broadcast Tests
# ---------------------------------------------------------------------------

class TestBroadcastSyncEvent:

    def setup_method(self):
        _entity_versions.clear()

    def test_broadcast_includes_actor(self):
        async def _test():
            mgr = ConnectionManager()
            ws = MockWebSocket()
            await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})

            with patch("app.websocket.manager", mgr):
                version = await broadcast_sync_event(
                    event_type="recommendation.approved",
                    entity_type="recommendation",
                    entity_id="rec-123",
                    data={"status": "Approved", "name": "John Doe"},
                    actor={"userId": "ops-1", "name": "Sarah", "role": "ops"},
                )

            assert version == 1

            sync_msgs = [m for m in ws.sent_messages
                         if m.get("event_type") == "recommendation.approved"]
            assert len(sync_msgs) == 1

            msg = sync_msgs[0]
            assert msg["entity_type"] == "recommendation"
            assert msg["entity_id"] == "rec-123"
            assert msg["version"] == 1
            assert msg["actor"]["userId"] == "ops-1"
            assert msg["actor"]["name"] == "Sarah"
            assert msg["actor"]["role"] == "ops"
            assert msg["data"]["status"] == "Approved"
            assert "timestamp" in msg
            assert "correlation_id" in msg

        run_async(_test())

    def test_broadcast_increments_version(self):
        async def _test():
            mgr = ConnectionManager()
            ws = MockWebSocket()
            await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})

            with patch("app.websocket.manager", mgr):
                v1 = await broadcast_sync_event(
                    event_type="technician.status_changed",
                    entity_type="technician",
                    entity_id="tech-1",
                    data={"status": "Ready Now"},
                )
                v2 = await broadcast_sync_event(
                    event_type="technician.status_changed",
                    entity_type="technician",
                    entity_id="tech-1",
                    data={"status": "In Training"},
                )

            assert v1 == 1
            assert v2 == 2

        run_async(_test())

    def test_broadcast_default_actor_is_system(self):
        async def _test():
            mgr = ConnectionManager()
            ws = MockWebSocket()
            await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})

            with patch("app.websocket.manager", mgr):
                await broadcast_sync_event(
                    event_type="training.completed",
                    entity_type="training",
                    entity_id="train-1",
                    data={},
                    # No actor provided — should default to system
                )

            sync_msgs = [m for m in ws.sent_messages
                         if m.get("event_type") == "training.completed"]
            assert len(sync_msgs) == 1
            assert sync_msgs[0]["actor"]["userId"] == "system"
            assert sync_msgs[0]["actor"]["name"] == "System"

        run_async(_test())

    def test_broadcast_uses_custom_topic(self):
        async def _test():
            mgr = ConnectionManager()
            ws_dash = MockWebSocket()
            ws_rec = MockWebSocket()

            await mgr.connect(ws_dash, "dashboard", user_context={"user_id": "u1", "role": "ops"})
            await mgr.connect(ws_rec, "recommendations", user_context={"user_id": "u2", "role": "ops"})

            with patch("app.websocket.manager", mgr):
                await broadcast_sync_event(
                    event_type="custom.event",
                    entity_type="project",
                    entity_id="proj-1",
                    data={"name": "Test Project"},
                    topic="dashboard",  # Override default topic
                )

            # Dashboard subscriber should get it
            dash_msgs = [m for m in ws_dash.sent_messages if m.get("event_type") == "custom.event"]
            assert len(dash_msgs) == 1

            # Recommendation subscriber should NOT get it
            rec_msgs = [m for m in ws_rec.sent_messages if m.get("event_type") == "custom.event"]
            assert len(rec_msgs) == 0

        run_async(_test())

    def test_broadcast_with_correlation_id(self):
        async def _test():
            mgr = ConnectionManager()
            ws = MockWebSocket()
            await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})

            with patch("app.websocket.manager", mgr):
                await broadcast_sync_event(
                    event_type="assignment.created",
                    entity_type="assignment",
                    entity_id="asgn-1",
                    data={},
                    correlation_id="trace-abc-123",
                )

            sync_msgs = [m for m in ws.sent_messages
                         if m.get("event_type") == "assignment.created"]
            assert sync_msgs[0]["correlation_id"] == "trace-abc-123"

        run_async(_test())


# ---------------------------------------------------------------------------
# Entity Type to Topic Mapping Tests
# ---------------------------------------------------------------------------

class TestEntityTypeToTopic:

    def test_recommendation_maps_to_recommendations(self):
        assert _entity_type_to_topic("recommendation") == "recommendations"

    def test_technician_maps_to_technicians(self):
        assert _entity_type_to_topic("technician") == "technicians"

    def test_assignment_maps_to_assignments(self):
        assert _entity_type_to_topic("assignment") == "assignments"

    def test_project_maps_to_dashboard(self):
        assert _entity_type_to_topic("project") == "dashboard"

    def test_timesheet_maps_to_timesheets(self):
        assert _entity_type_to_topic("timesheet") == "timesheets"

    def test_unknown_entity_maps_to_all(self):
        assert _entity_type_to_topic("unknown_entity") == "all"

    def test_badge_maps_to_badges(self):
        assert _entity_type_to_topic("badge") == "badges"

    def test_preference_rule_maps_to_recommendations(self):
        assert _entity_type_to_topic("preference_rule") == "recommendations"


# ---------------------------------------------------------------------------
# Actor Extraction Tests
# ---------------------------------------------------------------------------

class TestBuildActorFromRequest:

    def test_extract_from_demo_headers(self):
        request = MagicMock()
        request.headers = {
            "x-demo-user-id": "ops-2",
            "x-demo-user-name": "Jane Ops",
            "x-demo-role": "ops",
            "authorization": "",
        }

        actor = build_actor_from_request(request)
        assert actor["userId"] == "ops-2"
        assert actor["name"] == "Jane Ops"
        assert actor["role"] == "ops"

    def test_extract_from_jwt(self):
        request = MagicMock()
        request.headers = {
            "authorization": "Bearer valid-jwt",
            "x-demo-user-id": "fallback-id",
        }

        with patch("app.websocket._decode_ws_token") as mock_decode:
            mock_decode.return_value = {
                "user_id": "jwt-user-1",
                "name": "JWT User",
                "role": "partner",
            }
            actor = build_actor_from_request(request)

        assert actor["userId"] == "jwt-user-1"
        assert actor["name"] == "JWT User"
        assert actor["role"] == "partner"

    def test_fallback_on_invalid_jwt(self):
        request = MagicMock()
        request.headers = {
            "authorization": "Bearer bad-jwt",
            "x-demo-user-id": "fallback-id",
            "x-demo-user-name": "Fallback User",
            "x-demo-role": "technician",
        }

        with patch("app.websocket._decode_ws_token") as mock_decode:
            mock_decode.return_value = None
            actor = build_actor_from_request(request)

        assert actor["userId"] == "fallback-id"
        assert actor["name"] == "Fallback User"
        assert actor["role"] == "technician"

    def test_defaults_when_no_headers(self):
        request = MagicMock()
        request.headers = {}

        actor = build_actor_from_request(request)
        assert actor["userId"] == "ops-1"  # Default demo user
        assert actor["role"] == "ops"


# ---------------------------------------------------------------------------
# Concurrent Version Tracking Tests
# ---------------------------------------------------------------------------

class TestConcurrentVersioning:

    def setup_method(self):
        _entity_versions.clear()

    def test_concurrent_increments_are_serial(self):
        """Verify the lock ensures serial increment even with concurrent calls."""
        async def _test():
            tasks = [
                increment_entity_version("technician", "tech-1")
                for _ in range(10)
            ]
            results = await asyncio.gather(*tasks)

            # Should be 1-10 in some order
            assert sorted(results) == list(range(1, 11))
            final = await get_entity_version("technician", "tech-1")
            assert final == 10

        run_async(_test())

    def test_different_entities_are_independent(self):
        async def _test():
            await asyncio.gather(
                increment_entity_version("technician", "tech-1"),
                increment_entity_version("technician", "tech-2"),
                increment_entity_version("project", "proj-1"),
            )

            v1 = await get_entity_version("technician", "tech-1")
            v2 = await get_entity_version("technician", "tech-2")
            v3 = await get_entity_version("project", "proj-1")

            assert v1 == 1
            assert v2 == 1
            assert v3 == 1

        run_async(_test())


# ---------------------------------------------------------------------------
# Multi-User Conflict Scenario Tests
# ---------------------------------------------------------------------------

class TestMultiUserConflictScenarios:

    def setup_method(self):
        _entity_versions.clear()

    def test_two_users_modify_same_entity(self):
        """Simulate two users modifying the same recommendation.
        The second broadcast should have a higher version."""
        async def _test():
            mgr = ConnectionManager()
            ws1 = MockWebSocket()
            ws2 = MockWebSocket()

            await mgr.connect(ws1, "all", user_context={"user_id": "ops-1", "role": "ops"})
            await mgr.connect(ws2, "all", user_context={"user_id": "ops-2", "role": "ops"})

            with patch("app.websocket.manager", mgr):
                # User 1 approves
                v1 = await broadcast_sync_event(
                    event_type="recommendation.approved",
                    entity_type="recommendation",
                    entity_id="rec-1",
                    data={"status": "Approved"},
                    actor={"userId": "ops-1", "name": "Alice", "role": "ops"},
                )

                # User 2 also modifies (but gets version 2)
                v2 = await broadcast_sync_event(
                    event_type="recommendation.rejected",
                    entity_type="recommendation",
                    entity_id="rec-1",
                    data={"status": "Rejected"},
                    actor={"userId": "ops-2", "name": "Bob", "role": "ops"},
                )

            assert v1 == 1
            assert v2 == 2

            # Both users should have received both events
            ws1_events = [m for m in ws1.sent_messages if "recommendation" in m.get("event_type", "")]
            ws2_events = [m for m in ws2.sent_messages if "recommendation" in m.get("event_type", "")]

            # Each user receives both broadcasts (through "all" topic)
            assert len(ws1_events) == 2
            assert len(ws2_events) == 2

            # Version ordering is correct
            assert ws1_events[0]["version"] == 1
            assert ws1_events[1]["version"] == 2

            # Actor attribution is correct
            assert ws1_events[0]["actor"]["name"] == "Alice"
            assert ws1_events[1]["actor"]["name"] == "Bob"

        run_async(_test())

    def test_version_mismatch_detection(self):
        """Client can detect stale data by comparing local version with server."""
        async def _test():
            # Simulate: client reads version 0, server has version 2
            await increment_entity_version("technician", "tech-1")
            await increment_entity_version("technician", "tech-1")

            server_version = await get_entity_version("technician", "tech-1")
            client_version = 0  # Client has stale data

            # Client can detect the conflict
            assert server_version > client_version
            assert server_version == 2

        run_async(_test())

    def test_broadcast_to_multiple_topic_subscribers(self):
        """Sync event reaches both topic-specific and 'all' subscribers."""
        async def _test():
            mgr = ConnectionManager()
            ws_all = MockWebSocket()
            ws_topic = MockWebSocket()
            ws_other = MockWebSocket()

            await mgr.connect(ws_all, "all", user_context={"user_id": "u1", "role": "ops"})
            await mgr.connect(ws_topic, "technicians", user_context={"user_id": "u2", "role": "ops"})
            await mgr.connect(ws_other, "dashboard", user_context={"user_id": "u3", "role": "ops"})

            with patch("app.websocket.manager", mgr):
                await broadcast_sync_event(
                    event_type="technician.status_changed",
                    entity_type="technician",
                    entity_id="tech-1",
                    data={"status": "Ready Now"},
                    actor={"userId": "ops-1", "name": "Admin", "role": "ops"},
                )

            # "all" subscriber gets it
            all_msgs = [m for m in ws_all.sent_messages if m.get("event_type") == "technician.status_changed"]
            assert len(all_msgs) == 1

            # "technicians" subscriber gets it
            topic_msgs = [m for m in ws_topic.sent_messages if m.get("event_type") == "technician.status_changed"]
            assert len(topic_msgs) == 1

            # "dashboard" subscriber does NOT get it (different topic)
            other_msgs = [m for m in ws_other.sent_messages if m.get("event_type") == "technician.status_changed"]
            assert len(other_msgs) == 0

        run_async(_test())
