import asyncio
import json
import logging
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.routers import technicians
from app.routers import auth
from app.routers import chat
from app.routers import recommendations
from app.routers import staffing
from app.routers import advancement
from app.routers import partner_confirmations
from app.routers import partner
from app.routers import partner_portal
from app.routers import timesheets
from app.routers import escalations
from app.routers import disputes
from app.routers import partner_timesheets
from app.routers import career_passport
from app.routers import badges
from app.routers import forward_staffing
from app.routers import headcount
from app.routers import readiness
from app.routers import deployability
from app.routers import sync
from app.routers import notifications
from app.routers import skill_breakdowns
from app.routers import projects
from app.routers import portal
from app.routers import dashboard
from app.websocket import manager, authenticate_ws_query_param, topic_registry

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Deployable API",
    description="Agent-native workforce operating system for fiber/data center technicians",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow all origins for dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST Routers
app.include_router(auth.router)
app.include_router(badges.legacy_router)
app.include_router(badges.router)
app.include_router(technicians.router)
app.include_router(chat.router)
app.include_router(recommendations.router)
app.include_router(staffing.router)
app.include_router(advancement.router)
app.include_router(partner_confirmations.router)
app.include_router(partner.router)
app.include_router(partner_portal.router)
app.include_router(timesheets.router)
app.include_router(escalations.router)
app.include_router(disputes.router)
app.include_router(partner_timesheets.router)
app.include_router(career_passport.router)
app.include_router(forward_staffing.router)
app.include_router(headcount.router)
app.include_router(readiness.router)
app.include_router(deployability.router)
app.include_router(sync.router)
app.include_router(notifications.router)
app.include_router(skill_breakdowns.router)
app.include_router(projects.router)
app.include_router(portal.router)
app.include_router(dashboard.router)


# ---------------------------------------------------------------------------
# Convenience route: /passport/{token} -> public career passport HTML view
# This is the shareable URL that gets sent to partners, recruiters, etc.
# ---------------------------------------------------------------------------

from fastapi.responses import RedirectResponse


@app.get(
    "/passport/{token_value}",
    response_class=RedirectResponse,
    include_in_schema=False,
)
def passport_shortlink(token_value: str):
    """Redirect short shareable URL to the full public passport endpoint."""
    return RedirectResponse(
        url=f"/api/career-passport/public/{token_value}",
        status_code=302,
    )



# ---------------------------------------------------------------------------
# Redis pub/sub → WebSocket relay (for Celery worker notifications)
# ---------------------------------------------------------------------------

async def _redis_ws_relay():
    """Background task that subscribes to Redis pub/sub channel and relays
    messages to WebSocket clients via the ConnectionManager.

    Both Celery workers (sync publish) and sibling FastAPI workers (async
    publish inside ``ConnectionManager.broadcast``) push events to the
    ``deployable:ws_broadcast`` Redis channel.  This coroutine picks them
    up and broadcasts them to the appropriate local WebSocket topic
    subscribers.

    To prevent the *originating* worker from double-delivering a message
    it already sent locally, each envelope may carry an ``_origin`` worker
    ID.  If the origin matches *this* worker's ID, the message is skipped —
    this worker already delivered it inside ``broadcast()``.
    """
    import redis.asyncio as aioredis
    from app.websocket import _WORKER_ID

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    retry_delay = 1

    while True:
        try:
            r = aioredis.from_url(redis_url, decode_responses=True)
            pubsub = r.pubsub()
            await pubsub.subscribe("deployable:ws_broadcast")
            logger.info(
                "Redis WS relay: subscribed to deployable:ws_broadcast "
                "(worker_id=%s)",
                _WORKER_ID[:8],
            )
            retry_delay = 1  # Reset on successful connect

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    topic = data.get("topic", "all")
                    event = data.get("event", data)

                    # Skip messages that originated from this worker —
                    # they were already delivered locally in broadcast().
                    origin = data.get("_origin")
                    if origin == _WORKER_ID:
                        logger.debug(
                            "Skipping own broadcast for topic '%s'", topic
                        )
                        continue

                    # Forward optional filters so scoped/role broadcasts
                    # work correctly across workers.
                    role_filter = data.get("role_filter")
                    scoped_to = data.get("scoped_to")

                    await manager.broadcast(
                        topic,
                        event,
                        role_filter=role_filter,
                        scoped_to=scoped_to,
                        _from_redis=True,
                    )
                    logger.debug("Relayed WS event to topic '%s'", topic)
                except (json.JSONDecodeError, KeyError):
                    logger.warning("Invalid WS relay message: %s", message["data"][:200])
        except Exception:
            logger.warning(
                "Redis WS relay connection lost, reconnecting in %ds...",
                retry_delay,
                exc_info=True,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)


@app.on_event("startup")
async def seed_database():
    """Seed the database with initial data on first startup.

    Idempotent — skips records that already exist.
    """
    from app.database import SessionLocal
    from app.seeds.loader import seed_all

    db = SessionLocal()
    try:
        seed_all(db)
        logger.info("Database seed check complete")
    except Exception:
        db.rollback()
        logger.warning("Seed failed (tables may not exist yet — run migrations first)", exc_info=True)
    finally:
        db.close()


@app.on_event("startup")
async def start_redis_relay():
    """Start the Redis → WebSocket relay as a background task."""
    asyncio.create_task(_redis_ws_relay())


# ---------------------------------------------------------------------------
# WebSocket endpoint with topic-based subscriptions and JWT auth
# ---------------------------------------------------------------------------

@app.websocket("/ws/{topic}")
async def websocket_endpoint(websocket: WebSocket, topic: str):
    """WebSocket endpoint with topic-based subscriptions and authentication.

    Connect: ws://host/ws/{topic}?token=<jwt>
    Topics: recommendations, dashboard, technicians, confirmations, timesheets,
            assignments, training, escalations, forward_staffing, badges, partner, all

    After connecting, clients can dynamically subscribe/unsubscribe via JSON:
        {"action": "subscribe", "topics": ["recommendations", "dashboard"]}
        {"action": "unsubscribe", "topics": ["dashboard"]}
        {"action": "ping"}
        {"action": "list_topics"}
        {"action": "authenticate", "token": "<jwt>"}
    """
    # Authenticate via query param (or demo mode fallback)
    user_context = authenticate_ws_query_param(websocket)
    await manager.connect(websocket, topic, user_context=user_context)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.handle_client_message(websocket, data)
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)


@app.websocket("/ws")
async def websocket_default(websocket: WebSocket):
    """Default WebSocket endpoint subscribing to 'all' topics.

    Connect: ws://host/ws?token=<jwt>
    Supports the same JSON protocol as /ws/{topic}.
    """
    user_context = authenticate_ws_query_param(websocket)
    await manager.connect(websocket, "all", user_context=user_context)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.handle_client_message(websocket, data)
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Health & WS status
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/ws-status")
def ws_status():
    """Detailed WebSocket connection manager status."""
    return manager.get_status()


@app.get("/api/ws-topics")
def ws_topics():
    """List all registered WebSocket topics."""
    return {
        "topics": topic_registry.list_topics(),
    }
