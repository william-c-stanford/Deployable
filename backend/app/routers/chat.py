"""Chat API endpoints: send message (sync + SSE streaming), session management,
and headcount request confirmation."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.schemas.chat import (
    ChatHistoryResponse,
    ChatMessageResponse,
    ChatSendMessage,
    ChatSendResponse,
    ChatSessionCreate,
    ChatSessionDetail,
    ChatSessionResponse,
)
from app.services import chat_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Send a message (non-streaming)
# ---------------------------------------------------------------------------

@router.post("/messages", response_model=ChatSendResponse, status_code=status.HTTP_201_CREATED)
def send_message(
    body: ChatSendMessage,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Send a chat message (non-streaming).

    If `session_id` is provided, the message is appended to that session.
    Otherwise a new session is created automatically.

    Returns both the persisted user message and the assistant's reply.
    """
    # Convert UI state schema to plain dict for the service layer
    ui_state_dict = body.current_ui_state.model_dump() if body.current_ui_state else None

    try:
        user_msg, assistant_msg, session = chat_service.send_user_message(
            db=db,
            user_id=current_user.user_id,
            content=body.content,
            session_id=body.session_id,
            user_role=current_user.role,
            current_ui_state=ui_state_dict,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return ChatSendResponse(
        user_message=ChatMessageResponse.model_validate(user_msg),
        assistant_message=ChatMessageResponse.model_validate(assistant_msg),
        session_id=session.id,
    )


# ---------------------------------------------------------------------------
# Send a message with SSE streaming response
# ---------------------------------------------------------------------------

@router.post("/stream")
def send_message_stream(
    body: ChatSendMessage,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Send a chat message and receive a streamed assistant response via SSE.

    Returns a Server-Sent Events stream with:
    - event: token — individual response tokens (word by word)
    - event: ui_command — UI navigation/filter commands to execute
    - event: done — final event with full content for persistence

    The X-Session-Id response header contains the session UUID.
    """
    # Resolve or create session
    if body.session_id:
        session = chat_service.get_session(db, body.session_id, current_user.user_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        title = chat_service.auto_title_from_content(body.content)
        session = chat_service.create_session(db, current_user.user_id, title=title)

    # Convert UI state schema to plain dict for the service layer
    ui_state_dict = body.current_ui_state.model_dump() if body.current_ui_state else None

    # Persist user message
    chat_service.add_message(
        db=db,
        session_id=session.id,
        user_id=current_user.user_id,
        role="user",
        content=body.content,
    )
    db.commit()

    # Build message history for context
    existing = chat_service.get_messages(db, session.id, current_user.user_id)
    session_messages = [{"role": m.role, "content": m.content} for m in existing]

    # Create streaming response that also persists the assistant message on completion
    async def stream_and_persist():
        full_content = ""
        final_ui_commands = None
        final_metadata = None

        async for event in chat_service.generate_streaming_response(
            body.content,
            current_user.role,
            session_messages,
            db=db,
            session_id=session.id,
            user_id=current_user.user_id,
            current_ui_state=ui_state_dict,
        ):
            yield event

            # Parse done event to capture full content for DB persistence
            if event.startswith("event: done"):
                lines = event.strip().split("\n")
                for line in lines:
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            full_content = data.get("content", "")
                            final_ui_commands = data.get("ui_commands")
                            final_metadata = data.get("metadata")
                        except json.JSONDecodeError:
                            pass

        # Persist assistant message after streaming completes
        if full_content:
            from app.database import get_db as _get_db
            persist_db = next(_get_db())
            try:
                chat_service.add_message(
                    persist_db,
                    session.id,
                    current_user.user_id,
                    "assistant",
                    full_content,
                    ui_commands=final_ui_commands,
                    metadata=final_metadata,
                )
                persist_db.commit()
            finally:
                persist_db.close()

    headers = {
        "X-Session-Id": str(session.id),
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Access-Control-Expose-Headers": "X-Session-Id",
    }

    return StreamingResponse(
        stream_and_persist(),
        media_type="text/event-stream",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# List sessions
# ---------------------------------------------------------------------------

@router.get("/sessions", response_model=ChatHistoryResponse)
def list_sessions(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List chat sessions for the current user, newest first."""
    sessions, total = chat_service.list_sessions(
        db=db,
        user_id=current_user.user_id,
        skip=skip,
        limit=limit,
    )
    return ChatHistoryResponse(
        sessions=[ChatSessionResponse.model_validate(s) for s in sessions],
        total=total,
    )


# ---------------------------------------------------------------------------
# Get session detail (with messages)
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
def get_session(
    session_id: uuid.UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a specific session and its messages."""
    session = chat_service.get_session(db, session_id, current_user.user_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    messages = chat_service.get_messages(
        db=db,
        session_id=session_id,
        user_id=current_user.user_id,
        skip=skip,
        limit=limit,
    )
    return ChatSessionDetail(
        **ChatSessionResponse.model_validate(session).model_dump(),
        messages=[ChatMessageResponse.model_validate(m) for m in messages],
    )


# ---------------------------------------------------------------------------
# Create session explicitly
# ---------------------------------------------------------------------------

@router.post("/sessions", response_model=ChatSessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(
    body: ChatSessionCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new empty chat session."""
    session = chat_service.create_session(
        db=db,
        user_id=current_user.user_id,
        title=body.title,
    )
    db.commit()
    return ChatSessionResponse.model_validate(session)


# ---------------------------------------------------------------------------
# Delete session
# ---------------------------------------------------------------------------

@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a chat session and all its messages."""
    deleted = chat_service.delete_session(db, session_id, current_user.user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    db.commit()


# ---------------------------------------------------------------------------
# Get messages for a session
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageResponse])
def get_messages(
    session_id: uuid.UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get messages for a session."""
    session = chat_service.get_session(db, session_id, current_user.user_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    messages = chat_service.get_messages(
        db=db,
        session_id=session_id,
        user_id=current_user.user_id,
        skip=skip,
        limit=limit,
    )
    return [ChatMessageResponse.model_validate(m) for m in messages]


# ---------------------------------------------------------------------------
# Headcount request — direct confirmation endpoint
# ---------------------------------------------------------------------------

class HeadcountConfirmRequest(BaseModel):
    """Direct headcount request confirmation via structured form fallback."""
    role_name: str = Field(..., min_length=1, max_length=200)
    quantity: int = Field(1, ge=1, le=100)
    location: Optional[str] = None
    partner_id: Optional[str] = None
    project_id: Optional[str] = None
    start_date: Optional[date] = None
    constraints: Optional[str] = None
    notes: Optional[str] = None
    session_id: Optional[uuid.UUID] = None


class HeadcountConfirmResponse(BaseModel):
    """Response after confirming a headcount request."""
    headcount_request_id: str
    role_name: str
    quantity: int
    status: str
    message: str


@router.post(
    "/headcount/confirm",
    response_model=HeadcountConfirmResponse,
    status_code=status.HTTP_201_CREATED,
)
def confirm_headcount(
    body: HeadcountConfirmRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Confirm and create a PendingHeadcountRequest.

    This is the structured form fallback path of the two-path confirmation flow.
    Called when the user fills in a form with the headcount details instead of
    confirming via natural language in the chat.
    """
    try:
        headcount = chat_service.confirm_headcount_request(
            db=db,
            user_id=current_user.user_id,
            role_name=body.role_name,
            quantity=body.quantity,
            location=body.location,
            partner_id=body.partner_id,
            project_id=body.project_id,
            start_date=body.start_date,
            constraints=body.constraints,
            notes=body.notes,
        )

        # If a session is provided, add a confirmation message to the chat
        if body.session_id:
            session = chat_service.get_session(db, body.session_id, current_user.user_id)
            if session:
                chat_service.add_message(
                    db=db,
                    session_id=session.id,
                    user_id=current_user.user_id,
                    role="assistant",
                    content=(
                        f"Headcount request submitted via form:\n\n"
                        f"- **Role:** {headcount.role_name}\n"
                        f"- **Quantity:** {headcount.quantity}\n"
                        f"- **Status:** Pending review\n\n"
                        f"Request ID: `{str(headcount.id)[:8]}...`"
                    ),
                    ui_commands=[
                        {"action": "toast", "target": "success",
                         "params": {"message": f"Headcount request created: {headcount.quantity} {headcount.role_name}"}},
                    ],
                    metadata={
                        "model": "deployable-chat",
                        "agent": "chat-assistant",
                        "intent": "headcount_confirmed",
                        "headcount_request_id": str(headcount.id),
                        "confirmation_path": "structured_form",
                    },
                )

        db.commit()

        return HeadcountConfirmResponse(
            headcount_request_id=str(headcount.id),
            role_name=headcount.role_name,
            quantity=headcount.quantity,
            status=headcount.status,
            message=f"Headcount request for {headcount.quantity} {headcount.role_name} created successfully.",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )


# ---------------------------------------------------------------------------
# Headcount entity extraction — preview without confirmation
# ---------------------------------------------------------------------------

class HeadcountParseRequest(BaseModel):
    """Parse a natural language headcount request without confirming."""
    message: str = Field(..., min_length=1, max_length=2000)


class HeadcountParseResponse(BaseModel):
    """Parsed headcount entities from natural language."""
    is_headcount_intent: bool
    role: Optional[str] = None
    count: int = 1
    location: Optional[str] = None
    ready_to_confirm: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    matching_projects: list[dict] = Field(default_factory=list)
    partner: Optional[dict] = None


@router.post("/headcount/parse", response_model=HeadcountParseResponse)
def parse_headcount(
    body: HeadcountParseRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Parse a natural language message for headcount entities.

    Returns extracted entities (role, count, location) without creating
    a request. Useful for the frontend to build a confirmation UI.
    """
    entities = chat_service.extract_headcount_entities(body.message)

    if not entities:
        return HeadcountParseResponse(is_headcount_intent=False)

    role = entities.get("role")
    count = entities.get("count", 1)
    location = entities.get("location")

    missing = []
    if not role:
        missing.append("role")
    if not count or count <= 0:
        missing.append("quantity")

    # Look up matching projects/partners
    matching_projects = chat_service._find_matching_projects(db, location) if location else []
    partner = chat_service._find_matching_partner(db, location)

    return HeadcountParseResponse(
        is_headcount_intent=True,
        role=role,
        count=count,
        location=location,
        ready_to_confirm=len(missing) == 0,
        missing_fields=missing,
        matching_projects=matching_projects,
        partner=partner,
    )
