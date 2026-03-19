"""WebSocket broadcast service for publishing events from sync contexts.

This module provides a unified interface for Celery workers and API endpoints
to publish WebSocket notifications via Redis pub/sub. The FastAPI web process
subscribes to the Redis channel and relays messages to connected WebSocket clients.

Usage from Celery tasks (sync):
    from app.services.ws_broadcast import publish_ws_event, publish_recommendation_update, publish_badge_count_update

    publish_ws_event("recommendations", {
        "event_type": "recommendation.created",
        "data": {...}
    })

Usage from FastAPI endpoints (async):
    from app.services.ws_broadcast import broadcast_to_topic

    await broadcast_to_topic("recommendations", {
        "event_type": "recommendation.status_changed",
        "data": {...}
    })
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import redis

logger = logging.getLogger("deployable.ws_broadcast")

WS_BROADCAST_CHANNEL = "deployable:ws_broadcast"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Reuse a single Redis connection for pub/sub within the same process
_redis_client: Optional[redis.Redis] = None


def _get_redis() -> redis.Redis:
    """Get or create a Redis client for publishing."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def publish_ws_event(topic: str, event: dict[str, Any]) -> bool:
    """Publish a WebSocket event via Redis pub/sub (sync, for Celery workers).

    The FastAPI web process subscribes to the Redis channel and relays
    messages to connected WebSocket clients on the specified topic.

    Args:
        topic: WebSocket topic (e.g., "recommendations", "notifications", "dashboard")
        event: Event payload dict. Should include "event_type" key.

    Returns:
        True if published successfully, False otherwise.
    """
    try:
        r = _get_redis()
        # Add timestamp if not present
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()

        message = json.dumps({"topic": topic, "event": event}, default=str)
        r.publish(WS_BROADCAST_CHANNEL, message)
        logger.debug(
            "Published WS event to topic '%s': %s",
            topic,
            event.get("event_type", "unknown"),
        )
        return True
    except Exception:
        logger.warning("Failed to publish WS event to topic '%s'", topic, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Recommendation-specific broadcast helpers
# ---------------------------------------------------------------------------

def publish_recommendation_update(
    event_type: str,
    recommendation_data: dict[str, Any],
    pending_count: Optional[int] = None,
) -> bool:
    """Broadcast a recommendation list update to WebSocket subscribers.

    Args:
        event_type: One of "recommendation.created", "recommendation.updated",
                    "recommendation.status_changed", "recommendation.batch_refresh"
        recommendation_data: Serialized recommendation dict or summary
        pending_count: Optional updated pending count for badge updates
    """
    event = {
        "event_type": event_type,
        "topic": "recommendations",
        "recommendation": recommendation_data,
    }
    if pending_count is not None:
        event["pending_count"] = pending_count
    return publish_ws_event("recommendations", event)


def publish_recommendation_list_refresh(
    role_id: Optional[str] = None,
    project_id: Optional[str] = None,
    summary: Optional[dict[str, Any]] = None,
    pending_count: Optional[int] = None,
) -> bool:
    """Broadcast that the recommendation list should be refreshed.

    Used after batch operations (nightly refresh, rule changes, etc.)
    to tell connected clients to re-fetch their recommendation lists.
    """
    event: dict[str, Any] = {
        "event_type": "recommendation.list_refresh",
        "topic": "recommendations",
    }
    if role_id:
        event["role_id"] = role_id
    if project_id:
        event["project_id"] = project_id
    if summary:
        event["summary"] = summary
    if pending_count is not None:
        event["pending_count"] = pending_count
    return publish_ws_event("recommendations", event)


# ---------------------------------------------------------------------------
# Notification badge count broadcast helpers
# ---------------------------------------------------------------------------

def publish_badge_count_update(
    badge_type: str,
    count: int,
    role: str = "ops",
    details: Optional[dict[str, Any]] = None,
) -> bool:
    """Broadcast an updated badge count to the notifications topic.

    Args:
        badge_type: Type of badge (e.g., "pending_recommendations", "pending_confirmations",
                    "expiring_certs", "pending_timesheets", "escalations")
        count: The updated count
        role: Target role for this badge update ("ops", "partner", "technician")
        details: Optional additional details
    """
    event: dict[str, Any] = {
        "event_type": "badge_count.updated",
        "badge_type": badge_type,
        "count": count,
        "role": role,
    }
    if details:
        event["details"] = details
    return publish_ws_event("notifications", event)


def publish_notification(
    notification_type: str,
    title: str,
    message: str,
    role: str = "ops",
    severity: str = "info",
    link: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> bool:
    """Broadcast a notification to a specific role's notification topic.

    Args:
        notification_type: Category of notification
        title: Short notification title
        message: Notification body text
        role: Target role ("ops", "partner", "technician")
        severity: "info", "warning", "success", "error"
        link: Optional navigation link
        entity_type: Optional entity type for deep linking
        entity_id: Optional entity ID for deep linking
    """
    event: dict[str, Any] = {
        "event_type": "notification.created",
        "notification_type": notification_type,
        "title": title,
        "message": message,
        "role": role,
        "severity": severity,
    }
    if link:
        event["link"] = link
    if entity_type:
        event["entity_type"] = entity_type
    if entity_id:
        event["entity_id"] = entity_id
    return publish_ws_event("notifications", event)


# ---------------------------------------------------------------------------
# Skill breakdown broadcast helpers
# ---------------------------------------------------------------------------

def publish_skill_breakdown_event(
    event_type: str,
    breakdown_data: dict[str, Any],
    technician_id: Optional[str] = None,
    partner_id: Optional[str] = None,
) -> bool:
    """Broadcast a skill breakdown lifecycle event via Redis pub/sub (sync).

    Used by Celery workers to push skill breakdown events to WebSocket clients.

    Args:
        event_type: One of "skill_breakdown.submitted", "skill_breakdown.approved",
                    "skill_breakdown.rejected", "skill_breakdown.revision_requested"
        breakdown_data: Serialized skill breakdown dict
        technician_id: Optional technician ID for targeted delivery
        partner_id: Optional partner ID for partner-specific notification
    """
    event: dict[str, Any] = {
        "event_type": event_type,
        "topic": "skill_breakdowns",
        "skill_breakdown": breakdown_data,
    }
    if technician_id:
        event["technician_id"] = technician_id
    if partner_id:
        event["partner_id"] = partner_id

    # Broadcast to skill_breakdowns topic
    result = publish_ws_event("skill_breakdowns", event)

    # Also broadcast to partner topic if partner_id is provided
    if partner_id:
        partner_event = {**event, "topic": "partner"}
        publish_ws_event("partner", partner_event)

    return result


# ---------------------------------------------------------------------------
# Async broadcast helper (for FastAPI endpoints that already have access
# to the ConnectionManager)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Portal broadcast helpers (tech next steps + ops suggested actions)
# ---------------------------------------------------------------------------

def publish_tech_next_step_update(
    technician_id: str,
    event_type: str = "portal.next_step_updated",
    next_step_data: Optional[dict[str, Any]] = None,
    removed_step_id: Optional[str] = None,
    total_steps: int = 0,
) -> bool:
    """Broadcast a technician's next-step update to the tech_portal topic.

    Used when a recommendation targeting a technician is created, updated,
    or removed so the tech portal can refresh its "Your Next Step" panel.

    Args:
        technician_id: The technician whose next steps changed
        event_type: Event type string
        next_step_data: Serialized next step item (if added/updated)
        removed_step_id: ID of removed step (if removed)
        total_steps: Updated total count of active next steps
    """
    event: dict[str, Any] = {
        "event_type": event_type,
        "topic": "tech_portal",
        "technician_id": technician_id,
        "total_steps": total_steps,
    }
    if next_step_data:
        event["next_step"] = next_step_data
    if removed_step_id:
        event["removed_step_id"] = removed_step_id

    # Broadcast to tech_portal topic
    result = publish_ws_event("tech_portal", event)

    # Also broadcast to the training topic (which techs subscribe to)
    publish_ws_event("training", event)

    return result


def publish_ops_suggested_action_update(
    event_type: str = "portal.suggested_action_updated",
    action_data: Optional[dict[str, Any]] = None,
    removed_action_id: Optional[str] = None,
    total_actions: int = 0,
) -> bool:
    """Broadcast an ops suggested action update to the ops_portal topic.

    Used when suggested actions change (new recommendation created,
    action completed, batch refresh, etc.) so the ops dashboard can
    refresh its "Suggested Actions" panel.

    Args:
        event_type: Event type string
        action_data: Serialized action item (if added/updated)
        removed_action_id: ID of removed action (if removed)
        total_actions: Updated total count of action items
    """
    event: dict[str, Any] = {
        "event_type": event_type,
        "topic": "ops_portal",
        "total_actions": total_actions,
    }
    if action_data:
        event["action"] = action_data
    if removed_action_id:
        event["removed_action_id"] = removed_action_id

    # Broadcast to ops_portal topic
    result = publish_ws_event("ops_portal", event)

    # Also broadcast to dashboard topic for badge counts
    publish_ws_event("dashboard", event)

    return result


async def broadcast_to_topic(topic: str, event: dict[str, Any]) -> None:
    """Broadcast via the ConnectionManager (async, for FastAPI endpoints).

    Delivers to local WebSocket clients and publishes to Redis pub/sub
    for multi-worker fan-out so clients on other workers also receive
    the event.
    """
    from app.websocket import manager

    if "timestamp" not in event:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()

    await manager.broadcast(topic, event)
