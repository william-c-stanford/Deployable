"""Tests for WebSocket connection manager with topic-based subscriptions.

Covers:
- Topic registry validation and role-based access control
- Connection lifecycle (connect, disconnect, reconnect)
- Subscribe/unsubscribe protocol
- Broadcast with topic filtering, role filtering, scope filtering
- Authentication via token and late-auth
- Ping/pong heartbeat
- Error handling for invalid topics and messages
- Helper broadcast functions
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch

from app.websocket import (
    ConnectionManager,
    ConnectionState,
    TopicRegistry,
    TopicAccess,
    TopicDefinition,
    topic_registry,
    broadcast_recommendation_event,
    broadcast_confirmation_event,
    broadcast_dashboard_event,
    broadcast_technician_event,
    broadcast_training_event,
    broadcast_timesheet_event,
    broadcast_escalation_event,
    broadcast_badge_event,
    authenticate_ws_query_param,
    _decode_ws_token,
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
        self._client_state = MagicMock()
        self._query_params = query_params or {}

    @property
    def client_state(self):
        return self._client_state

    @property
    def query_params(self):
        return self._query_params

    async def accept(self):
        self.accepted = True

    async def send_text(self, data: str):
        self.sent_messages.append(json.loads(data))

    async def receive_text(self):
        await asyncio.sleep(100)
        return "ping"


# ---------------------------------------------------------------------------
# Topic Registry Tests
# ---------------------------------------------------------------------------

class TestTopicRegistry:

    def test_default_topics_registered(self):
        reg = TopicRegistry()
        assert reg.is_valid("all")
        assert reg.is_valid("recommendations")
        assert reg.is_valid("dashboard")
        assert reg.is_valid("technicians")
        assert reg.is_valid("confirmations")
        assert reg.is_valid("timesheets")
        assert reg.is_valid("assignments")
        assert reg.is_valid("training")
        assert reg.is_valid("escalations")
        assert reg.is_valid("forward_staffing")
        assert reg.is_valid("badges")
        assert reg.is_valid("partner")

    def test_invalid_topic(self):
        reg = TopicRegistry()
        assert not reg.is_valid("nonexistent")
        assert not reg.is_valid("")

    def test_register_custom_topic(self):
        reg = TopicRegistry()
        reg.register(TopicDefinition(
            name="custom",
            description="Test topic",
            access=TopicAccess.ALL,
        ))
        assert reg.is_valid("custom")

    def test_ops_can_access_all_topics(self):
        reg = TopicRegistry()
        for name in reg.topic_names:
            assert reg.can_access(name, "ops"), f"ops should access {name}"

    def test_technician_access_restrictions(self):
        reg = TopicRegistry()
        # Technicians CAN access these
        assert reg.can_access("all", "technician")
        assert reg.can_access("timesheets", "technician")
        assert reg.can_access("training", "technician")
        assert reg.can_access("badges", "technician")
        # Technicians CANNOT access these
        assert not reg.can_access("recommendations", "technician")
        assert not reg.can_access("dashboard", "technician")
        assert not reg.can_access("assignments", "technician")
        assert not reg.can_access("escalations", "technician")
        assert not reg.can_access("forward_staffing", "technician")

    def test_partner_access_restrictions(self):
        reg = TopicRegistry()
        assert reg.can_access("all", "partner")
        assert reg.can_access("confirmations", "partner")
        assert reg.can_access("timesheets", "partner")
        assert reg.can_access("partner", "partner")
        # Partners CANNOT access
        assert not reg.can_access("recommendations", "partner")
        assert not reg.can_access("dashboard", "partner")
        assert not reg.can_access("escalations", "partner")

    def test_list_topics_all(self):
        reg = TopicRegistry()
        topics = reg.list_topics()
        names = [t["name"] for t in topics]
        assert "all" in names
        assert "recommendations" in names

    def test_list_topics_filtered_by_role(self):
        reg = TopicRegistry()
        tech_topics = reg.list_topics(role="technician")
        names = [t["name"] for t in tech_topics]
        assert "training" in names
        assert "recommendations" not in names

    def test_get_topic_definition(self):
        reg = TopicRegistry()
        td = reg.get("recommendations")
        assert td is not None
        assert td.access == TopicAccess.OPS

    def test_get_nonexistent_topic(self):
        reg = TopicRegistry()
        assert reg.get("nonexistent") is None


# ---------------------------------------------------------------------------
# Connection Manager Tests
# ---------------------------------------------------------------------------

def _make_mgr():
    return ConnectionManager()


def _connected_state():
    from starlette.websockets import WebSocketState
    return WebSocketState.CONNECTED


def test_connect_with_user_context():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        state = await mgr.connect(ws, "recommendations", user_context={
            "user_id": "ops-1",
            "role": "ops",
            "name": "Test User",
        })

        assert ws.accepted
        assert state.user_id == "ops-1"
        assert state.role == "ops"
        assert state.authenticated is True
        assert "recommendations" in state.topics
        assert mgr.active_connections_count == 1

        # Should have received a welcome message
        assert len(ws.sent_messages) == 1
        assert ws.sent_messages[0]["type"] == "connection.established"
        assert ws.sent_messages[0]["user_id"] == "ops-1"

    run_async(_test())


def test_connect_without_auth_demo_mode():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        state = await mgr.connect(ws, "all")
        assert state.user_id == "anonymous"
        assert state.authenticated is False
        assert mgr.active_connections_count == 1

    run_async(_test())


def test_disconnect_full():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        await mgr.connect(ws, "recommendations", user_context={"user_id": "u1", "role": "ops"})
        assert mgr.active_connections_count == 1

        await mgr.disconnect(ws)
        assert mgr.active_connections_count == 0

    run_async(_test())


def test_disconnect_single_topic():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        state = await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})
        await mgr._subscribe_to_topic(state, "recommendations")

        assert "all" in state.topics
        assert "recommendations" in state.topics

        await mgr.disconnect(ws, topic="recommendations")
        assert "recommendations" not in state.topics
        assert "all" in state.topics
        assert mgr.active_connections_count == 1

    run_async(_test())


def test_broadcast_to_topic():
    async def _test():
        mgr = _make_mgr()
        ws1 = MockWebSocket()
        ws1._client_state = _connected_state()
        ws2 = MockWebSocket()
        ws2._client_state = _connected_state()

        await mgr.connect(ws1, "recommendations", user_context={"user_id": "u1", "role": "ops"})
        await mgr.connect(ws2, "dashboard", user_context={"user_id": "u2", "role": "ops"})

        await mgr.broadcast("recommendations", {"event": "test"})

        rec_msgs = [m for m in ws1.sent_messages if m.get("event") == "test"]
        assert len(rec_msgs) == 1
        dash_msgs = [m for m in ws2.sent_messages if m.get("event") == "test"]
        assert len(dash_msgs) == 0

    run_async(_test())


def test_broadcast_all_receives_everything():
    async def _test():
        mgr = _make_mgr()
        ws_all = MockWebSocket()
        ws_all._client_state = _connected_state()
        ws_specific = MockWebSocket()
        ws_specific._client_state = _connected_state()

        await mgr.connect(ws_all, "all", user_context={"user_id": "u1", "role": "ops"})
        await mgr.connect(ws_specific, "recommendations", user_context={"user_id": "u2", "role": "ops"})

        await mgr.broadcast("recommendations", {"event": "test"})

        all_events = [m for m in ws_all.sent_messages if m.get("event") == "test"]
        specific_events = [m for m in ws_specific.sent_messages if m.get("event") == "test"]
        assert len(all_events) == 1
        assert len(specific_events) == 1

    run_async(_test())


def test_broadcast_with_role_filter():
    async def _test():
        mgr = _make_mgr()
        ws_ops = MockWebSocket()
        ws_ops._client_state = _connected_state()
        ws_tech = MockWebSocket()
        ws_tech._client_state = _connected_state()

        await mgr.connect(ws_ops, "timesheets", user_context={"user_id": "u1", "role": "ops"})
        await mgr.connect(ws_tech, "timesheets", user_context={"user_id": "u2", "role": "technician"})

        await mgr.broadcast("timesheets", {"event": "ops_only"}, role_filter="ops")

        ops_events = [m for m in ws_ops.sent_messages if m.get("event") == "ops_only"]
        tech_events = [m for m in ws_tech.sent_messages if m.get("event") == "ops_only"]
        assert len(ops_events) == 1
        assert len(tech_events) == 0

    run_async(_test())


def test_broadcast_with_scope_filter():
    async def _test():
        mgr = _make_mgr()
        ws_p1 = MockWebSocket()
        ws_p1._client_state = _connected_state()
        ws_p2 = MockWebSocket()
        ws_p2._client_state = _connected_state()

        await mgr.connect(ws_p1, "partner", user_context={
            "user_id": "p1", "role": "partner", "scoped_to": "partner-1",
        })
        await mgr.connect(ws_p2, "partner", user_context={
            "user_id": "p2", "role": "partner", "scoped_to": "partner-2",
        })

        await mgr.broadcast("partner", {"event": "scoped"}, scoped_to="partner-1")

        p1_events = [m for m in ws_p1.sent_messages if m.get("event") == "scoped"]
        p2_events = [m for m in ws_p2.sent_messages if m.get("event") == "scoped"]
        assert len(p1_events) == 1
        assert len(p2_events) == 0

    run_async(_test())


def test_send_personal():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        await mgr.send_personal(ws, {"type": "pong"})
        assert len(ws.sent_messages) == 1
        assert ws.sent_messages[0]["type"] == "pong"

    run_async(_test())


def test_send_to_user():
    async def _test():
        mgr = _make_mgr()
        ws1 = MockWebSocket()
        ws1._client_state = _connected_state()
        ws2 = MockWebSocket()
        ws2._client_state = _connected_state()

        await mgr.connect(ws1, "all", user_context={"user_id": "u1", "role": "ops"})
        await mgr.connect(ws2, "all", user_context={"user_id": "u2", "role": "ops"})

        await mgr.send_to_user("u1", {"type": "personal"})

        u1_msgs = [m for m in ws1.sent_messages if m.get("type") == "personal"]
        u2_msgs = [m for m in ws2.sent_messages if m.get("type") == "personal"]
        assert len(u1_msgs) == 1
        assert len(u2_msgs) == 0

    run_async(_test())


# ---------------------------------------------------------------------------
# Protocol Message Handling Tests
# ---------------------------------------------------------------------------

def test_handle_ping_string():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})
        await mgr.handle_client_message(ws, "ping")

        pong_msgs = [m for m in ws.sent_messages if m.get("type") == "pong"]
        assert len(pong_msgs) == 1

    run_async(_test())


def test_handle_ping_json():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})
        await mgr.handle_client_message(ws, json.dumps({"action": "ping"}))

        pong_msgs = [m for m in ws.sent_messages if m.get("type") == "pong"]
        assert len(pong_msgs) == 1

    run_async(_test())


def test_handle_subscribe():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        state = await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})
        assert "recommendations" not in state.topics

        await mgr.handle_client_message(ws, json.dumps({
            "action": "subscribe",
            "topics": ["recommendations", "dashboard"],
        }))

        assert "recommendations" in state.topics
        assert "dashboard" in state.topics

        sub_msgs = [m for m in ws.sent_messages if m.get("type") == "subscribed"]
        assert len(sub_msgs) == 1
        assert "recommendations" in sub_msgs[0]["topics"]
        assert "dashboard" in sub_msgs[0]["topics"]

    run_async(_test())


def test_handle_subscribe_single_string():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        state = await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})
        await mgr.handle_client_message(ws, json.dumps({
            "action": "subscribe",
            "topics": "recommendations",
        }))
        assert "recommendations" in state.topics

    run_async(_test())


def test_handle_subscribe_invalid_topic():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})
        await mgr.handle_client_message(ws, json.dumps({
            "action": "subscribe",
            "topics": ["nonexistent"],
        }))

        error_msgs = [m for m in ws.sent_messages if m.get("type") == "error" and m.get("code") == "invalid_topic"]
        assert len(error_msgs) == 1

    run_async(_test())


def test_handle_subscribe_access_denied():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        await mgr.connect(ws, "all", user_context={"user_id": "t1", "role": "technician"})
        await mgr.handle_client_message(ws, json.dumps({
            "action": "subscribe",
            "topics": ["recommendations"],
        }))

        error_msgs = [m for m in ws.sent_messages if m.get("type") == "error" and m.get("code") == "access_denied"]
        assert len(error_msgs) == 1

    run_async(_test())


def test_handle_unsubscribe():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        state = await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})
        await mgr._subscribe_to_topic(state, "recommendations")
        assert "recommendations" in state.topics

        await mgr.handle_client_message(ws, json.dumps({
            "action": "unsubscribe",
            "topics": ["recommendations"],
        }))

        assert "recommendations" not in state.topics

        unsub_msgs = [m for m in ws.sent_messages if m.get("type") == "unsubscribed"]
        assert len(unsub_msgs) == 1

    run_async(_test())


def test_handle_list_topics():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})
        await mgr.handle_client_message(ws, json.dumps({"action": "list_topics"}))

        list_msgs = [m for m in ws.sent_messages if m.get("type") == "topic_list"]
        assert len(list_msgs) == 1
        assert "subscribed" in list_msgs[0]
        assert "available" in list_msgs[0]

    run_async(_test())


def test_handle_invalid_json():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})
        await mgr.handle_client_message(ws, "not valid json{")

        error_msgs = [m for m in ws.sent_messages if m.get("type") == "error" and m.get("code") == "invalid_json"]
        assert len(error_msgs) == 1

    run_async(_test())


def test_handle_unknown_action():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})
        await mgr.handle_client_message(ws, json.dumps({"action": "foobar"}))

        error_msgs = [m for m in ws.sent_messages if m.get("type") == "error" and m.get("code") == "unknown_action"]
        assert len(error_msgs) == 1

    run_async(_test())


def test_handle_late_authenticate():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        state = await mgr.connect(ws, "all")
        assert state.authenticated is False

        with patch("app.websocket._decode_ws_token") as mock_decode:
            mock_decode.return_value = {
                "user_id": "ops-2",
                "role": "ops",
                "scoped_to": None,
                "name": "Late Auth User",
            }
            await mgr.handle_client_message(ws, json.dumps({
                "action": "authenticate",
                "token": "valid-jwt-token",
            }))

        assert state.authenticated is True
        assert state.user_id == "ops-2"
        auth_msgs = [m for m in ws.sent_messages if m.get("type") == "authenticated"]
        assert len(auth_msgs) == 1

    run_async(_test())


def test_handle_late_authenticate_invalid_token():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        state = await mgr.connect(ws, "all")

        with patch("app.websocket._decode_ws_token") as mock_decode:
            mock_decode.return_value = None
            await mgr.handle_client_message(ws, json.dumps({
                "action": "authenticate",
                "token": "bad-token",
            }))

        assert state.authenticated is False
        error_msgs = [m for m in ws.sent_messages if m.get("type") == "error" and m.get("code") == "auth_failed"]
        assert len(error_msgs) == 1

    run_async(_test())


# ---------------------------------------------------------------------------
# Status and Properties
# ---------------------------------------------------------------------------

def test_get_status():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        await mgr.connect(ws, "recommendations", user_context={"user_id": "u1", "role": "ops"})

        status = mgr.get_status()
        assert status["total_connections"] == 1
        assert "recommendations" in status["topic_subscriptions"]
        assert status["topic_subscriptions"]["recommendations"] == 1
        assert "registered_topics" in status

    run_async(_test())


def test_connection_state_to_dict():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        state = await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})
        d = state.to_dict()
        assert d["user_id"] == "u1"
        assert d["role"] == "ops"
        assert d["authenticated"] is True

    run_async(_test())


# ---------------------------------------------------------------------------
# Helper Broadcast Functions
# ---------------------------------------------------------------------------

def test_broadcast_recommendation_event_helper():
    async def _test():
        mgr_local = ConnectionManager()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        await mgr_local.connect(ws, "recommendations", user_context={"user_id": "u1", "role": "ops"})

        with patch("app.websocket.manager", mgr_local):
            await broadcast_recommendation_event(
                "recommendation.created",
                {"id": "rec-1", "technician_name": "John"},
            )

        event_msgs = [m for m in ws.sent_messages if m.get("event_type") == "recommendation.created"]
        assert len(event_msgs) == 1
        assert event_msgs[0]["recommendation"]["id"] == "rec-1"

    run_async(_test())


def test_broadcast_dashboard_event_helper():
    async def _test():
        mgr_local = ConnectionManager()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        await mgr_local.connect(ws, "dashboard", user_context={"user_id": "u1", "role": "ops"})

        with patch("app.websocket.manager", mgr_local):
            await broadcast_dashboard_event("dashboard.metrics_updated", {"deployable_count": 42})

        event_msgs = [m for m in ws.sent_messages if m.get("event_type") == "dashboard.metrics_updated"]
        assert len(event_msgs) == 1

    run_async(_test())


def test_broadcast_confirmation_event_with_partner_scope():
    async def _test():
        mgr_local = ConnectionManager()
        ws_ops = MockWebSocket()
        ws_ops._client_state = _connected_state()
        ws_partner = MockWebSocket()
        ws_partner._client_state = _connected_state()

        await mgr_local.connect(ws_ops, "confirmations", user_context={"user_id": "u1", "role": "ops"})
        await mgr_local.connect(ws_partner, "partner", user_context={
            "user_id": "p1", "role": "partner", "scoped_to": "partner-1",
        })

        with patch("app.websocket.manager", mgr_local):
            await broadcast_confirmation_event(
                "confirmation.confirmed",
                {"id": "conf-1"},
                partner_id="partner-1",
            )

        ops_events = [m for m in ws_ops.sent_messages if m.get("event_type") == "confirmation.confirmed"]
        assert len(ops_events) == 1

        partner_events = [m for m in ws_partner.sent_messages if m.get("event_type") == "confirmation.confirmed"]
        assert len(partner_events) == 1

    run_async(_test())


# ---------------------------------------------------------------------------
# Authentication Helper Tests
# ---------------------------------------------------------------------------

class TestAuthenticateWSQueryParam:

    def test_no_token_returns_demo_user(self):
        ws = MockWebSocket(query_params={})
        result = authenticate_ws_query_param(ws)
        assert result is not None
        assert result["user_id"] == "ops-1"
        assert result["role"] == "ops"

    def test_with_valid_token(self):
        ws = MockWebSocket(query_params={"token": "test-jwt"})
        with patch("app.websocket._decode_ws_token") as mock_decode:
            mock_decode.return_value = {"user_id": "u1", "role": "partner"}
            result = authenticate_ws_query_param(ws)
        assert result["user_id"] == "u1"
        assert result["role"] == "partner"

    def test_with_invalid_token(self):
        ws = MockWebSocket(query_params={"token": "bad-jwt"})
        with patch("app.websocket._decode_ws_token") as mock_decode:
            mock_decode.return_value = None
            result = authenticate_ws_query_param(ws)
        assert result is None


# ---------------------------------------------------------------------------
# Deduplication and multi-topic tests
# ---------------------------------------------------------------------------

def test_broadcast_deduplicates_across_all_and_topic():
    """A connection subscribed to both 'all' and a specific topic should
    receive a broadcast only once."""
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        state = await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})
        await mgr._subscribe_to_topic(state, "recommendations")

        await mgr.broadcast("recommendations", {"event": "dedup_test"})

        dedup_msgs = [m for m in ws.sent_messages if m.get("event") == "dedup_test"]
        assert len(dedup_msgs) == 1  # Not 2

    run_async(_test())


def test_multiple_topics_per_connection():
    async def _test():
        mgr = _make_mgr()
        ws = MockWebSocket()
        ws._client_state = _connected_state()

        state = await mgr.connect(ws, "all", user_context={"user_id": "u1", "role": "ops"})
        await mgr._subscribe_to_topic(state, "recommendations")
        await mgr._subscribe_to_topic(state, "dashboard")
        await mgr._subscribe_to_topic(state, "technicians")

        assert len(state.topics) == 4  # all + 3 specific
        assert mgr.active_connections_count == 1

    run_async(_test())
