"""WebSocket connection manager with topic-based subscription management.

Features:
- Topic registry with role-based access control
- JWT authentication on connect (via query param or first message)
- Dynamic subscribe/unsubscribe protocol via JSON messages
- Per-connection state tracking with user context
- Broadcast to topic subscribers with role filtering
- Redis pub/sub for multi-worker fan-out
- Heartbeat/ping-pong keepalive
- Dead connection cleanup

Topics: "recommendations", "dashboard", "technicians", "confirmations",
         "timesheets", "assignments", "training", "escalations",
         "forward_staffing", "badges", "partner", "all"
"""

import asyncio
import json
import logging
import os
import uuid as _uuid_mod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis pub/sub for multi-worker fan-out
# ---------------------------------------------------------------------------

WS_BROADCAST_CHANNEL = "deployable:ws_broadcast"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Lazy-initialized async Redis client for publishing from the event loop
_async_redis_publisher = None
_publisher_lock = asyncio.Lock()


async def _get_async_redis():
    """Get or create an async Redis client for publishing broadcast events."""
    global _async_redis_publisher
    if _async_redis_publisher is None:
        async with _publisher_lock:
            if _async_redis_publisher is None:
                import redis.asyncio as aioredis
                _async_redis_publisher = aioredis.from_url(
                    REDIS_URL, decode_responses=True
                )
    return _async_redis_publisher


# Unique worker ID to prevent re-processing our own published messages
_WORKER_ID = str(_uuid_mod.uuid4())


# ---------------------------------------------------------------------------
# Topic Registry
# ---------------------------------------------------------------------------

class TopicAccess(str, Enum):
    """Who can subscribe to a topic."""
    ALL = "all"        # Any authenticated user
    OPS = "ops"        # Ops users only
    TECH = "tech"      # Technicians only
    PARTNER = "partner"  # Partners only
    OPS_PARTNER = "ops_partner"  # Ops or partner


@dataclass
class TopicDefinition:
    """Metadata for a registered WebSocket topic."""
    name: str
    description: str
    access: TopicAccess = TopicAccess.ALL
    # If True, events on this topic also broadcast to "all" subscribers
    propagate_to_all: bool = True


class TopicRegistry:
    """Central registry of valid WebSocket topics with access control."""

    def __init__(self):
        self._topics: dict[str, TopicDefinition] = {}
        self._register_defaults()

    def _register_defaults(self):
        """Register all built-in topics."""
        defaults = [
            TopicDefinition(
                name="all",
                description="Catch-all topic receiving events from all other topics",
                access=TopicAccess.ALL,
                propagate_to_all=False,
            ),
            TopicDefinition(
                name="recommendations",
                description="Staffing recommendation lifecycle events",
                access=TopicAccess.OPS,
            ),
            TopicDefinition(
                name="dashboard",
                description="Dashboard metric updates and alerts",
                access=TopicAccess.OPS,
            ),
            TopicDefinition(
                name="technicians",
                description="Technician profile and status changes",
                access=TopicAccess.OPS,
            ),
            TopicDefinition(
                name="confirmations",
                description="Partner assignment confirmation status changes",
                access=TopicAccess.OPS_PARTNER,
            ),
            TopicDefinition(
                name="timesheets",
                description="Timesheet submission, approval, and flagging events",
                access=TopicAccess.ALL,
            ),
            TopicDefinition(
                name="assignments",
                description="Assignment lifecycle events",
                access=TopicAccess.OPS,
            ),
            TopicDefinition(
                name="training",
                description="Training advancement and proficiency events",
                access=TopicAccess.ALL,
            ),
            TopicDefinition(
                name="escalations",
                description="Escalation alerts and resolution events",
                access=TopicAccess.OPS,
            ),
            TopicDefinition(
                name="forward_staffing",
                description="Forward staffing gap detection and chaining events",
                access=TopicAccess.OPS,
            ),
            TopicDefinition(
                name="badges",
                description="Badge awarded/milestone events",
                access=TopicAccess.ALL,
            ),
            TopicDefinition(
                name="partner",
                description="Partner-specific notifications and updates",
                access=TopicAccess.PARTNER,
            ),
            TopicDefinition(
                name="notifications",
                description="Badge count updates and notification alerts for all roles",
                access=TopicAccess.ALL,
            ),
            TopicDefinition(
                name="skill_breakdowns",
                description="Skill breakdown submission, approval, rejection, and revision events",
                access=TopicAccess.ALL,
            ),
            TopicDefinition(
                name="tech_portal",
                description="Technician portal next-step updates and career recommendations",
                access=TopicAccess.TECH,
            ),
            TopicDefinition(
                name="ops_portal",
                description="Ops dashboard suggested actions and pending recommendation updates",
                access=TopicAccess.OPS,
            ),
        ]
        for td in defaults:
            self._topics[td.name] = td

    def register(self, topic_def: TopicDefinition):
        """Register a new topic (for extensibility)."""
        self._topics[topic_def.name] = topic_def

    def get(self, name: str) -> Optional[TopicDefinition]:
        """Get topic definition by name."""
        return self._topics.get(name)

    def is_valid(self, name: str) -> bool:
        """Check if a topic name is registered."""
        return name in self._topics

    def can_access(self, topic_name: str, role: str) -> bool:
        """Check if a role can subscribe to a topic."""
        topic = self._topics.get(topic_name)
        if not topic:
            return False
        if topic.access == TopicAccess.ALL:
            return True
        if topic.access == TopicAccess.OPS and role == "ops":
            return True
        if topic.access == TopicAccess.TECH and role == "technician":
            return True
        if topic.access == TopicAccess.PARTNER and role == "partner":
            return True
        if topic.access == TopicAccess.OPS_PARTNER and role in ("ops", "partner"):
            return True
        # Ops can always access all topics
        if role == "ops":
            return True
        return False

    def list_topics(self, role: Optional[str] = None) -> list[dict[str, Any]]:
        """List all topics, optionally filtered by role access."""
        result = []
        for td in self._topics.values():
            if role and not self.can_access(td.name, role):
                continue
            result.append({
                "name": td.name,
                "description": td.description,
                "access": td.access.value,
            })
        return result

    @property
    def topic_names(self) -> list[str]:
        return list(self._topics.keys())


# Singleton registry
topic_registry = TopicRegistry()


# ---------------------------------------------------------------------------
# Connection State
# ---------------------------------------------------------------------------

@dataclass(eq=False)
class ConnectionState:
    """Per-connection state tracking.

    Identity is based on the websocket object (id-based), making it
    hashable and usable in sets despite mutable fields.
    """
    websocket: WebSocket
    user_id: str = "anonymous"
    role: str = "ops"
    scoped_to: Optional[str] = None  # partner_id or technician_id scope
    name: str = "Anonymous"
    topics: set[str] = field(default_factory=set)
    connected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    authenticated: bool = False
    last_ping: Optional[str] = None

    def __hash__(self) -> int:
        return id(self.websocket)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ConnectionState):
            return NotImplemented
        return self.websocket is other.websocket

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "role": self.role,
            "topics": list(self.topics),
            "connected_at": self.connected_at,
            "authenticated": self.authenticated,
        }


# ---------------------------------------------------------------------------
# Connection Manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages WebSocket connections with topic-based pub/sub.

    Protocol:
    - Client connects to /ws or /ws/{topic}
    - Optionally authenticates via ?token=<jwt> query param
    - Can dynamically subscribe/unsubscribe via JSON messages:
        {"action": "subscribe", "topics": ["recommendations", "dashboard"]}
        {"action": "unsubscribe", "topics": ["dashboard"]}
        {"action": "ping"}
        {"action": "list_topics"}
    - Server pushes events as JSON:
        {"event_type": "...", "topic": "...", "data": {...}, "timestamp": "..."}
    """

    def __init__(self):
        # topic -> set of ConnectionState
        self._subscriptions: dict[str, set[ConnectionState]] = {}
        # websocket -> ConnectionState
        self._connections: dict[WebSocket, ConnectionState] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        initial_topic: str = "all",
        user_context: Optional[dict] = None,
    ) -> ConnectionState:
        """Accept a websocket connection, authenticate, and subscribe to initial topic."""
        await websocket.accept()

        # Build connection state from user context (JWT-derived)
        state = ConnectionState(websocket=websocket)
        if user_context:
            state.user_id = user_context.get("user_id", "anonymous")
            state.role = user_context.get("role", "ops")
            state.scoped_to = user_context.get("scoped_to")
            state.name = user_context.get("name", "Anonymous")
            state.authenticated = True

        async with self._lock:
            self._connections[websocket] = state

        # Subscribe to initial topic if access is allowed
        await self._subscribe_to_topic(state, initial_topic)

        logger.info(
            "WebSocket connected: user=%s role=%s topic=%s authenticated=%s",
            state.user_id, state.role, initial_topic, state.authenticated,
        )

        # Send welcome message with connection info
        await self.send_personal(websocket, {
            "type": "connection.established",
            "user_id": state.user_id,
            "role": state.role,
            "topics": list(state.topics),
            "available_topics": topic_registry.list_topics(state.role),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return state

    async def disconnect(self, websocket: WebSocket, topic: Optional[str] = None):
        """Remove a websocket from all (or specific) topic subscriptions."""
        async with self._lock:
            state = self._connections.get(websocket)
            if not state:
                return

            if topic:
                # Unsubscribe from specific topic
                state.topics.discard(topic)
                if topic in self._subscriptions:
                    self._subscriptions[topic].discard(state)
                    if not self._subscriptions[topic]:
                        del self._subscriptions[topic]
            else:
                # Full disconnect — remove from all topics
                for t in list(state.topics):
                    if t in self._subscriptions:
                        self._subscriptions[t].discard(state)
                        if not self._subscriptions[t]:
                            del self._subscriptions[t]
                state.topics.clear()
                del self._connections[websocket]

        logger.info(
            "WebSocket disconnected: user=%s topic=%s",
            state.user_id if state else "unknown",
            topic or "all",
        )

    async def _subscribe_to_topic(self, state: ConnectionState, topic: str) -> bool:
        """Internal: subscribe a connection to a topic with access check."""
        if not topic_registry.is_valid(topic):
            await self.send_personal(state.websocket, {
                "type": "error",
                "code": "invalid_topic",
                "message": f"Unknown topic: {topic}",
                "available_topics": topic_registry.topic_names,
            })
            return False

        if not topic_registry.can_access(topic, state.role):
            await self.send_personal(state.websocket, {
                "type": "error",
                "code": "access_denied",
                "message": f"Role '{state.role}' cannot subscribe to topic '{topic}'",
            })
            return False

        async with self._lock:
            if topic not in self._subscriptions:
                self._subscriptions[topic] = set()
            self._subscriptions[topic].add(state)
            state.topics.add(topic)

        return True

    async def handle_client_message(self, websocket: WebSocket, raw_data: str):
        """Process a message from a connected client.

        Protocol messages:
        - {"action": "subscribe", "topics": ["topic1", "topic2"]}
        - {"action": "unsubscribe", "topics": ["topic1"]}
        - {"action": "ping"}
        - {"action": "list_topics"}
        """
        state = self._connections.get(websocket)
        if not state:
            return

        # Handle simple ping string
        if raw_data.strip() == "ping":
            state.last_ping = datetime.now(timezone.utc).isoformat()
            await self.send_personal(websocket, {"type": "pong"})
            return

        # Parse JSON message
        try:
            message = json.loads(raw_data)
        except json.JSONDecodeError:
            await self.send_personal(websocket, {
                "type": "error",
                "code": "invalid_json",
                "message": "Message must be valid JSON or 'ping'",
            })
            return

        action = message.get("action", "").lower()

        if action == "ping":
            state.last_ping = datetime.now(timezone.utc).isoformat()
            await self.send_personal(websocket, {"type": "pong"})

        elif action == "subscribe":
            topics = message.get("topics", [])
            if isinstance(topics, str):
                topics = [topics]
            subscribed = []
            for t in topics:
                if await self._subscribe_to_topic(state, t):
                    subscribed.append(t)
            await self.send_personal(websocket, {
                "type": "subscribed",
                "topics": subscribed,
                "all_topics": list(state.topics),
            })

        elif action == "unsubscribe":
            topics = message.get("topics", [])
            if isinstance(topics, str):
                topics = [topics]
            unsubscribed = []
            for t in topics:
                if t in state.topics:
                    await self.disconnect(websocket, topic=t)
                    unsubscribed.append(t)
            await self.send_personal(websocket, {
                "type": "unsubscribed",
                "topics": unsubscribed,
                "all_topics": list(state.topics),
            })

        elif action == "list_topics":
            await self.send_personal(websocket, {
                "type": "topic_list",
                "subscribed": list(state.topics),
                "available": topic_registry.list_topics(state.role),
            })

        elif action == "authenticate":
            # Late authentication (if not done via query param)
            token = message.get("token")
            if token:
                user_ctx = _decode_ws_token(token)
                if user_ctx:
                    state.user_id = user_ctx.get("user_id", state.user_id)
                    state.role = user_ctx.get("role", state.role)
                    state.scoped_to = user_ctx.get("scoped_to")
                    state.name = user_ctx.get("name", state.name)
                    state.authenticated = True
                    await self.send_personal(websocket, {
                        "type": "authenticated",
                        "user_id": state.user_id,
                        "role": state.role,
                        "available_topics": topic_registry.list_topics(state.role),
                    })
                else:
                    await self.send_personal(websocket, {
                        "type": "error",
                        "code": "auth_failed",
                        "message": "Invalid or expired token",
                    })

        else:
            # Unknown action — log for debugging
            logger.debug("Unknown WS action '%s' from user %s", action, state.user_id)
            await self.send_personal(websocket, {
                "type": "error",
                "code": "unknown_action",
                "message": f"Unknown action: {action}",
                "supported_actions": [
                    "ping", "subscribe", "unsubscribe", "list_topics", "authenticate"
                ],
            })

    async def broadcast(
        self,
        topic: str,
        message: dict[str, Any],
        *,
        role_filter: Optional[str] = None,
        scoped_to: Optional[str] = None,
        _from_redis: bool = False,
    ):
        """Broadcast a message to all subscribers of a topic (and 'all' topic).

        When _from_redis is False (default), the message is also published to
        the Redis ``deployable:ws_broadcast`` channel so that other FastAPI
        workers receive it and relay to *their* local WebSocket clients.  This
        enables multi-worker fan-out behind a load balancer.

        When the Redis relay task in main.py receives a message, it calls
        ``broadcast(..., _from_redis=True)`` to deliver locally **without**
        re-publishing — preventing infinite loops.

        Args:
            topic: The topic to broadcast to.
            message: The event payload dict.
            role_filter: Only send to connections with this role.
            scoped_to: Only send to connections scoped to this entity ID.
            _from_redis: Internal flag — True when the call originates from
                         the Redis relay subscriber.  Never set this manually.
        """
        # --- Multi-worker fan-out: publish to Redis so other workers relay ---
        if not _from_redis:
            try:
                r = await _get_async_redis()
                envelope = json.dumps(
                    {
                        "topic": topic,
                        "event": message,
                        "role_filter": role_filter,
                        "scoped_to": scoped_to,
                        "_origin": _WORKER_ID,
                    },
                    default=str,
                )
                await r.publish(WS_BROADCAST_CHANNEL, envelope)
                logger.debug(
                    "Published broadcast to Redis channel '%s' for topic '%s'",
                    WS_BROADCAST_CHANNEL,
                    topic,
                )
            except Exception:
                logger.warning(
                    "Failed to publish broadcast to Redis for topic '%s'; "
                    "delivering locally only",
                    topic,
                    exc_info=True,
                )

        # --- Deliver to local WebSocket connections ---
        targets: list[ConnectionState] = []

        async with self._lock:
            # Subscribers to the specific topic
            if topic in self._subscriptions:
                targets.extend(self._subscriptions[topic])
            # Subscribers to "all" also get every message (if topic propagates)
            topic_def = topic_registry.get(topic)
            if topic != "all" and (not topic_def or topic_def.propagate_to_all):
                if "all" in self._subscriptions:
                    targets.extend(self._subscriptions["all"])

        if not targets:
            return

        # Apply filters
        if role_filter:
            targets = [s for s in targets if s.role == role_filter]
        if scoped_to:
            targets = [s for s in targets if s.scoped_to == scoped_to or s.scoped_to is None]

        # Deduplicate (a connection may appear in both topic and "all")
        seen_ws: set[int] = set()
        unique_targets: list[ConnectionState] = []
        for s in targets:
            ws_id = id(s.websocket)
            if ws_id not in seen_ws:
                seen_ws.add(ws_id)
                unique_targets.append(s)

        payload = json.dumps(message, default=str)
        dead: list[ConnectionState] = []

        for state in unique_targets:
            try:
                if state.websocket.client_state == WebSocketState.CONNECTED:
                    await state.websocket.send_text(payload)
            except Exception:
                dead.append(state)

        # Clean up dead connections
        if dead:
            for state in dead:
                await self.disconnect(state.websocket)

    async def broadcast_to_role(
        self,
        topic: str,
        message: dict[str, Any],
        role: str,
    ):
        """Convenience: broadcast to a topic filtered by role."""
        await self.broadcast(topic, message, role_filter=role)

    async def broadcast_to_scoped(
        self,
        topic: str,
        message: dict[str, Any],
        scoped_to: str,
    ):
        """Convenience: broadcast to connections scoped to a specific entity."""
        await self.broadcast(topic, message, scoped_to=scoped_to)

    async def send_personal(self, websocket: WebSocket, message: dict[str, Any]):
        """Send a message to a specific websocket."""
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(json.dumps(message, default=str))
        except Exception:
            logger.warning("Failed to send personal message to websocket")

    async def send_to_user(self, user_id: str, message: dict[str, Any]):
        """Send a message to all connections for a specific user."""
        targets: list[WebSocket] = []
        async with self._lock:
            for ws, state in self._connections.items():
                if state.user_id == user_id:
                    targets.append(ws)

        for ws in targets:
            await self.send_personal(ws, message)

    def get_connection_state(self, websocket: WebSocket) -> Optional[ConnectionState]:
        """Get the state for a specific connection."""
        return self._connections.get(websocket)

    @property
    def active_connections_count(self) -> int:
        return len(self._connections)

    @property
    def topics(self) -> list[str]:
        return list(self._subscriptions.keys())

    def get_status(self) -> dict[str, Any]:
        """Get detailed status of the connection manager."""
        topic_counts = {}
        for topic, subs in self._subscriptions.items():
            topic_counts[topic] = len(subs)
        return {
            "total_connections": len(self._connections),
            "topic_subscriptions": topic_counts,
            "registered_topics": topic_registry.topic_names,
        }


# ---------------------------------------------------------------------------
# JWT Helper for WebSocket auth
# ---------------------------------------------------------------------------

def _decode_ws_token(token: str) -> Optional[dict]:
    """Decode a JWT token for WebSocket authentication.

    Returns user context dict or None if invalid.
    """
    try:
        from app.core.config import settings
        from jose import JWTError, jwt as jose_jwt
        payload = jose_jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        return {
            "user_id": payload.get("user_id", payload.get("sub", "anonymous")),
            "role": payload.get("role", "ops"),
            "scoped_to": payload.get("scoped_to"),
            "name": payload.get("name", "Unknown"),
        }
    except (JWTError, Exception) as e:
        logger.warning("WebSocket JWT decode failed: %s", e)
        return None


def authenticate_ws_query_param(websocket: WebSocket) -> Optional[dict]:
    """Extract and decode JWT from WebSocket query parameters.

    Clients connect with: ws://host/ws/topic?token=<jwt>
    Returns user context dict or None.
    """
    token = websocket.query_params.get("token")
    if not token:
        # Demo mode: return default ops user (matches REST API behavior)
        return {
            "user_id": "ops-1",
            "role": "ops",
            "scoped_to": None,
            "name": "Demo Ops User",
        }
    return _decode_ws_token(token)


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------

manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Helper broadcast functions (used by routers and workers)
# ---------------------------------------------------------------------------

async def broadcast_recommendation_event(
    event_type: str,
    recommendation_data: dict[str, Any],
):
    """Helper to broadcast a recommendation event to WebSocket subscribers.

    Args:
        event_type: One of "recommendation.created", "recommendation.updated",
                    "recommendation.status_changed"
        recommendation_data: Serialized recommendation dict
    """
    event = {
        "event_type": event_type,
        "topic": "recommendations",
        "recommendation": recommendation_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast("recommendations", event)
    logger.info(
        "Broadcast %s for recommendation %s",
        event_type,
        recommendation_data.get("id"),
    )


async def broadcast_confirmation_event(
    event_type: str,
    confirmation_data: dict[str, Any],
    partner_id: Optional[str] = None,
):
    """Broadcast confirmation status changes to appropriate subscribers."""
    event = {
        "event_type": event_type,
        "topic": "confirmations",
        "confirmation": confirmation_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast("confirmations", event)

    # Also send to partner-specific channel if scoped
    if partner_id:
        await manager.broadcast("partner", event, scoped_to=partner_id)


async def broadcast_dashboard_event(
    event_type: str,
    data: dict[str, Any],
):
    """Broadcast dashboard metric updates."""
    event = {
        "event_type": event_type,
        "topic": "dashboard",
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast("dashboard", event)


async def broadcast_technician_event(
    event_type: str,
    technician_data: dict[str, Any],
):
    """Broadcast technician profile/status changes."""
    event = {
        "event_type": event_type,
        "topic": "technicians",
        "technician": technician_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast("technicians", event)


async def broadcast_training_event(
    event_type: str,
    training_data: dict[str, Any],
    technician_id: Optional[str] = None,
):
    """Broadcast training advancement events."""
    event = {
        "event_type": event_type,
        "topic": "training",
        "training": training_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast("training", event)


async def broadcast_timesheet_event(
    event_type: str,
    timesheet_data: dict[str, Any],
):
    """Broadcast timesheet lifecycle events."""
    event = {
        "event_type": event_type,
        "topic": "timesheets",
        "timesheet": timesheet_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast("timesheets", event)


async def broadcast_escalation_event(
    event_type: str,
    escalation_data: dict[str, Any],
):
    """Broadcast escalation events to ops."""
    event = {
        "event_type": event_type,
        "topic": "escalations",
        "escalation": escalation_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast("escalations", event)


async def broadcast_skill_breakdown_event(
    event_type: str,
    breakdown_data: dict[str, Any],
    technician_id: Optional[str] = None,
    partner_id: Optional[str] = None,
):
    """Broadcast skill breakdown lifecycle events.

    Sends to the skill_breakdowns topic (visible to all roles) and
    also to the partner topic if a partner_id is provided so partners
    see real-time updates.

    Args:
        event_type: One of "skill_breakdown.submitted", "skill_breakdown.approved",
                    "skill_breakdown.rejected", "skill_breakdown.revision_requested"
        breakdown_data: Serialized skill breakdown dict
        technician_id: Optional technician ID for targeted delivery
        partner_id: Optional partner ID for partner-specific notification
    """
    event = {
        "event_type": event_type,
        "topic": "skill_breakdowns",
        "skill_breakdown": breakdown_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast("skill_breakdowns", event)

    # Also push to partner-specific channel so partner dashboard updates
    if partner_id:
        await manager.broadcast("partner", event, scoped_to=partner_id)

    logger.info(
        "Broadcast %s for skill_breakdown %s (tech=%s, partner=%s)",
        event_type,
        breakdown_data.get("id"),
        technician_id,
        partner_id,
    )


async def broadcast_badge_event(
    event_type: str,
    badge_data: dict[str, Any],
):
    """Broadcast badge awarded/milestone events."""
    event = {
        "event_type": event_type,
        "topic": "badges",
        "badge": badge_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast("badges", event)


# ---------------------------------------------------------------------------
# Multi-user sync broadcast with actor attribution + version tracking
# ---------------------------------------------------------------------------

# In-memory version counters (per entity). In production, use DB column.
_entity_versions: dict[str, int] = {}
_entity_versions_lock = asyncio.Lock()


async def get_entity_version(entity_type: str, entity_id: str) -> int:
    """Get the current version for an entity."""
    key = f"{entity_type}:{entity_id}"
    return _entity_versions.get(key, 0)


async def increment_entity_version(entity_type: str, entity_id: str) -> int:
    """Atomically increment and return the new version for an entity."""
    key = f"{entity_type}:{entity_id}"
    async with _entity_versions_lock:
        current = _entity_versions.get(key, 0)
        new_version = current + 1
        _entity_versions[key] = new_version
    return new_version


async def broadcast_sync_event(
    event_type: str,
    entity_type: str,
    entity_id: str,
    data: dict[str, Any],
    actor: Optional[dict[str, Any]] = None,
    topic: Optional[str] = None,
    correlation_id: Optional[str] = None,
):
    """Broadcast a state-sync event with actor attribution and version tracking.

    This is the primary broadcast function for multi-user state sync.
    All mutation events should use this to ensure all connected clients
    see who made what change and can do conflict resolution.

    Args:
        event_type: Domain event type (e.g., "recommendation.approved")
        entity_type: Entity kind (e.g., "recommendation", "technician")
        entity_id: Entity primary key
        data: Serialized entity data (current state after mutation)
        actor: Who triggered the change: {"userId": "...", "name": "...", "role": "..."}
        topic: WebSocket topic to broadcast to. Defaults to entity_type + "s" or "all".
        correlation_id: For tracing cascading events.
    """
    import uuid as _uuid

    # Increment version
    version = await increment_entity_version(entity_type, entity_id)

    # Determine topic
    target_topic = topic or _entity_type_to_topic(entity_type)

    event = {
        "event_type": event_type,
        "topic": target_topic,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "version": version,
        "data": data,
        "actor": actor or {"userId": "system", "name": "System", "role": "system"},
        "actor_id": (actor or {}).get("userId", "system"),
        "actor_name": (actor or {}).get("name", "System"),
        "correlation_id": correlation_id or str(_uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await manager.broadcast(target_topic, event)
    logger.info(
        "Sync broadcast: %s %s:%s v%d by %s",
        event_type,
        entity_type,
        entity_id[:8],
        version,
        (actor or {}).get("name", "system"),
    )
    return version


def _entity_type_to_topic(entity_type: str) -> str:
    """Map entity type to default WebSocket topic."""
    mapping = {
        "recommendation": "recommendations",
        "technician": "technicians",
        "assignment": "assignments",
        "confirmation": "confirmations",
        "timesheet": "timesheets",
        "training": "training",
        "escalation": "escalations",
        "badge": "badges",
        "project": "dashboard",
        "preference_rule": "recommendations",
        "forward_staffing_gap": "forward_staffing",
        "skill_breakdown": "skill_breakdowns",
    }
    return mapping.get(entity_type, "all")


def build_actor_from_request(request) -> dict[str, Any]:
    """Extract actor info from a FastAPI request for sync broadcasts.

    Works with both JWT-authenticated and demo-header requests.
    """
    actor = {
        "userId": "unknown",
        "name": "Unknown User",
        "role": "ops",
    }

    # Try JWT first (standard auth)
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user_ctx = _decode_ws_token(token)
        if user_ctx:
            actor["userId"] = user_ctx.get("user_id", "unknown")
            actor["name"] = user_ctx.get("name", "Unknown")
            actor["role"] = user_ctx.get("role", "ops")
            return actor

    # Fallback to demo headers
    actor["userId"] = request.headers.get("x-demo-user-id", "ops-1")
    actor["name"] = request.headers.get("x-demo-user-name", "Demo User")
    actor["role"] = request.headers.get("x-demo-role", "ops")

    return actor
