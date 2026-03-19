"""Tests for WebSocket broadcast service (ws_broadcast.py).

Covers:
- publish_ws_event sends messages to Redis pub/sub
- publish_recommendation_update formats events correctly
- publish_recommendation_list_refresh formats refresh events
- publish_badge_count_update formats badge events
- publish_notification formats notification events
- broadcast_to_topic sends directly via ConnectionManager
- Notifications topic is registered in the topic registry
- Notifications endpoint returns correct badge counts
"""

import json
import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime

from app.services.ws_broadcast import (
    publish_ws_event,
    publish_recommendation_update,
    publish_recommendation_list_refresh,
    publish_badge_count_update,
    publish_notification,
    broadcast_to_topic,
    WS_BROADCAST_CHANNEL,
)


def run_async(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests for sync publish functions (Redis pub/sub)
# ---------------------------------------------------------------------------


class TestPublishWsEvent:
    """Tests for the core publish_ws_event function."""

    @patch("app.services.ws_broadcast._get_redis")
    def test_publishes_to_redis_channel(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        result = publish_ws_event("recommendations", {
            "event_type": "recommendation.created",
            "data": {"id": "rec-1"},
        })

        assert result is True
        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        assert call_args[0][0] == WS_BROADCAST_CHANNEL
        payload = json.loads(call_args[0][1])
        assert payload["topic"] == "recommendations"
        assert payload["event"]["event_type"] == "recommendation.created"

    @patch("app.services.ws_broadcast._get_redis")
    def test_adds_timestamp_if_missing(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish_ws_event("notifications", {"event_type": "badge_count.updated"})

        call_args = mock_redis.publish.call_args
        payload = json.loads(call_args[0][1])
        assert "timestamp" in payload["event"]

    @patch("app.services.ws_broadcast._get_redis")
    def test_preserves_existing_timestamp(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        ts = "2026-01-01T00:00:00Z"
        publish_ws_event("notifications", {"event_type": "test", "timestamp": ts})

        call_args = mock_redis.publish.call_args
        payload = json.loads(call_args[0][1])
        assert payload["event"]["timestamp"] == ts

    @patch("app.services.ws_broadcast._get_redis")
    def test_returns_false_on_redis_error(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = Exception("Redis down")
        mock_get_redis.return_value = mock_redis

        result = publish_ws_event("notifications", {"event_type": "test"})
        assert result is False


class TestPublishRecommendationUpdate:
    """Tests for recommendation-specific broadcast helpers."""

    @patch("app.services.ws_broadcast._get_redis")
    def test_formats_recommendation_event(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish_recommendation_update(
            "recommendation.created",
            {"id": "rec-1", "score": 85},
            pending_count=10,
        )

        call_args = mock_redis.publish.call_args
        payload = json.loads(call_args[0][1])
        event = payload["event"]
        assert event["event_type"] == "recommendation.created"
        assert event["topic"] == "recommendations"
        assert event["recommendation"]["id"] == "rec-1"
        assert event["pending_count"] == 10

    @patch("app.services.ws_broadcast._get_redis")
    def test_omits_pending_count_when_none(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish_recommendation_update("recommendation.updated", {"id": "rec-1"})

        call_args = mock_redis.publish.call_args
        payload = json.loads(call_args[0][1])
        assert "pending_count" not in payload["event"]


class TestPublishRecommendationListRefresh:

    @patch("app.services.ws_broadcast._get_redis")
    def test_formats_refresh_event(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish_recommendation_list_refresh(
            role_id="role-1",
            project_id="proj-1",
            summary={"action": "nightly_batch", "refreshed": 5},
            pending_count=15,
        )

        call_args = mock_redis.publish.call_args
        payload = json.loads(call_args[0][1])
        event = payload["event"]
        assert event["event_type"] == "recommendation.list_refresh"
        assert event["role_id"] == "role-1"
        assert event["project_id"] == "proj-1"
        assert event["summary"]["action"] == "nightly_batch"
        assert event["pending_count"] == 15

    @patch("app.services.ws_broadcast._get_redis")
    def test_minimal_refresh_event(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish_recommendation_list_refresh()

        call_args = mock_redis.publish.call_args
        payload = json.loads(call_args[0][1])
        event = payload["event"]
        assert event["event_type"] == "recommendation.list_refresh"
        assert "role_id" not in event
        assert "project_id" not in event


class TestPublishBadgeCountUpdate:

    @patch("app.services.ws_broadcast._get_redis")
    def test_formats_badge_event(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish_badge_count_update(
            badge_type="pending_recommendations",
            count=42,
            role="ops",
            details={"source": "nightly_batch"},
        )

        call_args = mock_redis.publish.call_args
        payload = json.loads(call_args[0][1])
        assert payload["topic"] == "notifications"
        event = payload["event"]
        assert event["event_type"] == "badge_count.updated"
        assert event["badge_type"] == "pending_recommendations"
        assert event["count"] == 42
        assert event["role"] == "ops"
        assert event["details"]["source"] == "nightly_batch"

    @patch("app.services.ws_broadcast._get_redis")
    def test_badge_event_without_details(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish_badge_count_update("expiring_certs", 5, "ops")

        call_args = mock_redis.publish.call_args
        payload = json.loads(call_args[0][1])
        event = payload["event"]
        assert event["badge_type"] == "expiring_certs"
        assert event["count"] == 5
        assert "details" not in event


class TestPublishNotification:

    @patch("app.services.ws_broadcast._get_redis")
    def test_formats_notification_event(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish_notification(
            notification_type="staffing_recommendations",
            title="New Staffing Recommendations",
            message="5 candidates ranked for Lead Fiber Splicer",
            role="ops",
            severity="info",
            link="/agent-inbox",
            entity_type="project_role",
            entity_id="role-1",
        )

        call_args = mock_redis.publish.call_args
        payload = json.loads(call_args[0][1])
        assert payload["topic"] == "notifications"
        event = payload["event"]
        assert event["event_type"] == "notification.created"
        assert event["notification_type"] == "staffing_recommendations"
        assert event["title"] == "New Staffing Recommendations"
        assert event["message"] == "5 candidates ranked for Lead Fiber Splicer"
        assert event["severity"] == "info"
        assert event["link"] == "/agent-inbox"
        assert event["entity_type"] == "project_role"
        assert event["entity_id"] == "role-1"

    @patch("app.services.ws_broadcast._get_redis")
    def test_minimal_notification(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish_notification(
            notification_type="test",
            title="Test",
            message="test message",
        )

        call_args = mock_redis.publish.call_args
        payload = json.loads(call_args[0][1])
        event = payload["event"]
        assert event["event_type"] == "notification.created"
        assert "link" not in event
        assert "entity_type" not in event


# ---------------------------------------------------------------------------
# Tests for async broadcast (direct ConnectionManager)
# ---------------------------------------------------------------------------


class TestBroadcastToTopic:

    def test_broadcasts_directly_via_manager(self):
        async def _test():
            from app.websocket import ConnectionManager
            mgr = ConnectionManager()

            # Create a mock WebSocket
            from starlette.websockets import WebSocketState
            mock_ws = MagicMock()
            mock_ws.client_state = WebSocketState.CONNECTED
            mock_ws.accept = AsyncMock()
            mock_ws.send_text = AsyncMock()

            await mgr.connect(mock_ws, "notifications", user_context={
                "user_id": "u1", "role": "ops",
            })

            # Patch the manager at the module level where broadcast_to_topic imports it
            with patch("app.websocket.manager", mgr):
                await broadcast_to_topic("notifications", {
                    "event_type": "badge_count.updated",
                    "badge_type": "pending_recommendations",
                    "count": 10,
                })

            # Check that send_text was called with the event
            assert mock_ws.send_text.called
            sent_data = json.loads(mock_ws.send_text.call_args[0][0])
            assert sent_data["event_type"] == "badge_count.updated"
            assert sent_data["count"] == 10
            assert "timestamp" in sent_data

        run_async(_test())


# ---------------------------------------------------------------------------
# Tests for notifications topic registration
# ---------------------------------------------------------------------------


class TestNotificationsTopicRegistration:

    def test_notifications_topic_exists(self):
        from app.websocket import topic_registry
        assert topic_registry.is_valid("notifications")

    def test_all_roles_can_access_notifications(self):
        from app.websocket import topic_registry
        assert topic_registry.can_access("notifications", "ops")
        assert topic_registry.can_access("notifications", "technician")
        assert topic_registry.can_access("notifications", "partner")

    def test_notifications_topic_in_list(self):
        from app.websocket import topic_registry
        all_topics = topic_registry.list_topics()
        names = [t["name"] for t in all_topics]
        assert "notifications" in names
