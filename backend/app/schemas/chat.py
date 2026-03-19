"""Pydantic schemas for chat endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class UIStateContext(BaseModel):
    """Snapshot of the user's current UI state sent with each chat message.

    This allows the LangChain agent to give context-aware, filter-aware
    responses.  For example, if the user is already on the technician
    directory with a "Ready Now" filter active, the agent can reference
    that context instead of asking the user to navigate there.
    """

    current_route: Optional[str] = Field(
        None,
        description="Current URL path, e.g. '/ops/technicians'",
    )
    active_filters: Optional[Dict[str, str]] = Field(
        default_factory=dict,
        description="Active URL search-param filters, e.g. {'status': 'Ready Now', 'region': 'Texas'}",
    )
    active_tab: Optional[str] = Field(
        None,
        description="Currently selected tab on the page, e.g. 'recommendations'",
    )
    selected_entity_id: Optional[str] = Field(
        None,
        description="ID of the currently selected/viewed entity (technician, project, etc.)",
    )
    selected_entity_type: Optional[str] = Field(
        None,
        description="Type of the selected entity, e.g. 'technician', 'project'",
    )
    viewport: Optional[str] = Field(
        None,
        description="Device viewport hint: 'mobile', 'tablet', or 'desktop'",
    )


class ChatSendMessage(BaseModel):
    """Payload for sending a new chat message."""

    content: str = Field(..., min_length=1, max_length=4000)
    session_id: Optional[uuid.UUID] = None  # None → create new session
    current_ui_state: Optional[UIStateContext] = Field(
        None,
        description="Snapshot of the user's current UI state for context-aware responses",
    )


class ChatSessionCreate(BaseModel):
    """Explicitly create a new empty session (optional; auto-created on first message)."""

    title: Optional[str] = None


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class ChatMessageResponse(BaseModel):
    """Single chat message."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    session_id: uuid.UUID
    user_id: str
    role: str
    content: str
    ui_commands: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def extract_metadata(cls, data: Any) -> Any:
        """Handle SQLAlchemy column named 'metadata_' mapped to DB column 'metadata'."""
        if hasattr(data, "__dict__"):
            # ORM object — read metadata_ attribute directly
            raw = getattr(data, "metadata_", None)
            # Avoid picking up SQLAlchemy MetaData objects
            if raw is not None and not isinstance(raw, dict):
                raw = None
            return {
                "id": data.id,
                "session_id": data.session_id,
                "user_id": data.user_id,
                "role": data.role,
                "content": data.content,
                "ui_commands": data.ui_commands,
                "metadata": raw,
                "created_at": data.created_at,
            }
        return data


class ChatSessionResponse(BaseModel):
    """Session summary (no messages)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: str
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ChatSessionDetail(ChatSessionResponse):
    """Session with messages included."""

    messages: List[ChatMessageResponse] = Field(default_factory=list)


class ChatHistoryResponse(BaseModel):
    """Paginated list of sessions for a user."""

    sessions: List[ChatSessionResponse]
    total: int


class ChatSendResponse(BaseModel):
    """Response after sending a message: echoes back user msg + assistant reply."""

    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse
    session_id: uuid.UUID
