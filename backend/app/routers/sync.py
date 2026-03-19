"""Multi-user state sync router.

Provides:
- Entity version queries for conflict detection
- Sync status endpoint
- Force-refresh trigger for clients to re-fetch stale data
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.websocket import (
    manager,
    get_entity_version,
    broadcast_sync_event,
    build_actor_from_request,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class EntityVersionRequest(BaseModel):
    entity_type: str
    entity_id: str


class EntityVersionResponse(BaseModel):
    entity_type: str
    entity_id: str
    version: int


class BulkVersionRequest(BaseModel):
    entities: list[EntityVersionRequest]


class BulkVersionResponse(BaseModel):
    versions: list[EntityVersionResponse]


class SyncBroadcastRequest(BaseModel):
    """Manual sync broadcast — useful for triggering UI refreshes."""
    event_type: str
    entity_type: str
    entity_id: str
    data: dict[str, Any] = {}
    topic: Optional[str] = None


class SyncStatusResponse(BaseModel):
    connected_clients: int
    topics: list[str]
    topic_counts: dict[str, int]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status", response_model=SyncStatusResponse)
async def sync_status():
    """Get current sync system status — active connections and topics."""
    status = manager.get_status()
    return SyncStatusResponse(
        connected_clients=status["total_connections"],
        topics=status["registered_topics"],
        topic_counts=status.get("topic_subscriptions", {}),
    )


@router.post("/version", response_model=EntityVersionResponse)
async def get_version(req: EntityVersionRequest):
    """Get the current version of an entity for conflict detection."""
    version = await get_entity_version(req.entity_type, req.entity_id)
    return EntityVersionResponse(
        entity_type=req.entity_type,
        entity_id=req.entity_id,
        version=version,
    )


@router.post("/versions", response_model=BulkVersionResponse)
async def get_versions(req: BulkVersionRequest):
    """Get versions for multiple entities at once."""
    versions = []
    for entity in req.entities:
        version = await get_entity_version(entity.entity_type, entity.entity_id)
        versions.append(EntityVersionResponse(
            entity_type=entity.entity_type,
            entity_id=entity.entity_id,
            version=version,
        ))
    return BulkVersionResponse(versions=versions)


@router.post("/broadcast")
async def manual_broadcast(req: SyncBroadcastRequest, request: Request):
    """Manually trigger a sync broadcast.

    Used by API endpoints that want to notify other clients about a change.
    Automatically extracts actor info from the request JWT/headers.
    """
    actor = build_actor_from_request(request)
    version = await broadcast_sync_event(
        event_type=req.event_type,
        entity_type=req.entity_type,
        entity_id=req.entity_id,
        data=req.data,
        actor=actor,
        topic=req.topic,
    )
    return {
        "status": "broadcast_sent",
        "version": version,
        "actor": actor,
    }


@router.post("/refresh-all")
async def trigger_refresh(request: Request):
    """Trigger a global refresh signal for all connected clients.

    Useful after bulk operations (seed data load, nightly batch, etc.)
    """
    actor = build_actor_from_request(request)
    await manager.broadcast("all", {
        "event_type": "sync.refresh_requested",
        "topic": "all",
        "entity_type": "system",
        "entity_id": "global",
        "actor": actor,
        "data": {"reason": "manual_refresh"},
    })
    return {"status": "refresh_broadcast_sent"}
