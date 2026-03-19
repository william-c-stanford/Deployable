"""Chat service: persistence layer for chat sessions and messages,
plus intent parsing and SSE streaming response generation.

Streaming uses the Anthropic SDK to call Claude claude-3-5-sonnet in real-time,
yielding token-by-token SSE events and emitting structured ui_commands."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, date, timezone, timedelta
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.chat import ChatMessage, ChatSession
from app.models.audit import PendingHeadcountRequest, HeadcountRequestStatus
from app.models.project import Project
from app.models.user import Partner

logger = logging.getLogger("deployable.chat")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Anthropic LLM client (lazy-loaded, async for streaming)
# ---------------------------------------------------------------------------

CHAT_MODEL = os.getenv("CHAT_MODEL", "claude-3-5-sonnet-20241022")
CHAT_MAX_TOKENS = int(os.getenv("CHAT_MAX_TOKENS", "1024"))
CHAT_TEMPERATURE = float(os.getenv("CHAT_TEMPERATURE", "0.3"))

_async_client = None


def _get_async_anthropic_client():
    """Lazy-load an async Anthropic client for streaming chat."""
    global _async_client
    if _async_client is not None:
        return _async_client
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — LLM streaming unavailable, using fallback")
        return None
    try:
        import anthropic
        _async_client = anthropic.AsyncAnthropic(api_key=api_key)
        return _async_client
    except ImportError:
        logger.warning("anthropic package not installed — using fallback streaming")
        return None


# ---------------------------------------------------------------------------
# System prompt for chat LLM — includes the command manifest
# ---------------------------------------------------------------------------

CHAT_SYSTEM_PROMPT = """You are the Deployable assistant — an AI copilot for fiber/data-center workforce operations.
You help operations managers, technicians, and partners navigate the platform, filter data, answer workforce questions, and take action.

## Your capabilities
- Navigate the user to any screen in the app
- Filter technician directories, projects, training pipelines
- Answer questions about technician counts, project status, availability
- Create headcount requests via natural language
- Provide workforce insights and recommendations

## Personality
- Concise, professional, helpful
- Use markdown formatting for readability (bold, bullets, etc.)
- Be specific — reference actual data when possible
- Don't over-explain — users are domain experts

## UI Commands
You can drive the UI by including a JSON block in your response. When you want to trigger
navigation, filtering, or toasts, include EXACTLY ONE fenced block at the END of your message:

```ui_commands
[{"type": "navigate", "target": "/ops/dashboard", "label": "Open Dashboard"}]
```

Command types:
- "navigate" — go to a route
- "filter" — replace all filters on a page
- "add_filter" — merge new filter params (additive)
- "remove_filter" — remove specific filter params
- "clear_filters" — remove all filters
- "open_detail" — open a detail view
- "set_tab" — switch tab
- "toast" — show a notification (target = "success"|"info"|"warning"|"error", params.message = text)

Available routes:
- /ops/dashboard — Operations Dashboard
- /ops/technicians — Technician Directory
- /ops/technicians/:id — Technician Profile
- /ops/training — Training Pipeline
- /ops/projects — Project Staffing
- /ops/projects/:id — Project Detail
- /ops/inbox — Agent Inbox
- /tech/portal — Technician Portal

Valid filter values:
- career_stage: Sourced, Screened, In Training, Training Completed, Awaiting Assignment, Deployed
- deployability_status: Ready Now, In Training, Currently Assigned, Missing Cert, Missing Docs, Rolling Off Soon, Inactive
- project_status: Draft, Staffing, Active, Wrapping Up, Closed
- inbox_tab: recommendations, rules, activity

Filter params for /ops/technicians: search, career_stage, deployability_status, region, skill, available_before
Filter params for /ops/projects: search, status, region, partner
Filter params for /ops/training: stage, search
Filter params for /ops/inbox: tab, type, agent

## Important rules
1. Only include ui_commands when the user's intent requires UI action
2. For simple Q&A, just respond with text — no ui_commands needed
3. Never fabricate specific technician names, IDs, or exact counts unless you have data
4. If the user is already on the target screen (check UI context), skip navigation and just adjust filters
5. Keep responses concise — 1-3 short paragraphs max for most queries
"""


def _build_llm_messages(
    user_message: str,
    session_messages: list[dict],
    user_role: str,
    ui_context_summary: Optional[str] = None,
) -> list[dict]:
    """Build the messages array for the Anthropic API call.

    Includes recent conversation history (last 20 messages) plus the new
    user message, with optional UI context injected.
    """
    messages: list[dict] = []

    # Include recent history (up to 20 messages) for conversational context
    history = session_messages[-20:] if len(session_messages) > 20 else session_messages
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    # Build the new user message with optional UI context
    user_content = user_message
    if ui_context_summary:
        user_content = f"[UI Context: {ui_context_summary}]\n\n{user_message}"

    # Add role context
    role_label = {"ops": "operations manager", "tech": "technician", "partner": "partner"}.get(
        user_role, "user"
    )
    user_content = f"[User role: {role_label}]\n{user_content}"

    messages.append({"role": "user", "content": user_content})

    # Ensure messages start with a user message (Anthropic API requirement)
    if messages and messages[0]["role"] != "user":
        messages = messages[1:]

    return messages


def _parse_ui_commands_from_text(text: str) -> Tuple[str, list[dict]]:
    """Extract ui_commands JSON block from LLM response text.

    The LLM is instructed to put commands in a fenced block:
    ```ui_commands
    [...]
    ```

    Returns (clean_text, commands_list).
    """
    pattern = r"```ui_commands\s*\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return text, []

    try:
        commands = json.loads(match.group(1).strip())
        if not isinstance(commands, list):
            commands = [commands]
        # Validate each command has required fields
        valid_commands = []
        valid_types = {"navigate", "filter", "add_filter", "remove_filter",
                       "clear_filters", "highlight", "open_detail", "set_tab",
                       "scroll_to", "toast"}
        for cmd in commands:
            if isinstance(cmd, dict) and cmd.get("type") in valid_types and cmd.get("target"):
                valid_commands.append(cmd)
        # Remove the command block from the visible text
        clean_text = text[:match.start()].rstrip() + text[match.end():].lstrip()
        return clean_text.strip(), valid_commands
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse ui_commands from LLM response")
        return text, []


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def create_session(
    db: Session,
    user_id: str,
    title: Optional[str] = None,
) -> ChatSession:
    """Create a new chat session."""
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user_id,
        title=title,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(session)
    db.flush()
    return session


def get_session(
    db: Session,
    session_id: uuid.UUID,
    user_id: str,
) -> Optional[ChatSession]:
    """Get a session by ID, scoped to user."""
    return (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
        .first()
    )


def list_sessions(
    db: Session,
    user_id: str,
    skip: int = 0,
    limit: int = 50,
) -> Tuple[List[ChatSession], int]:
    """List sessions for a user, newest first."""
    q = db.query(ChatSession).filter(ChatSession.user_id == user_id)
    total = q.count()
    sessions = (
        q.order_by(ChatSession.updated_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return sessions, total


def delete_session(
    db: Session,
    session_id: uuid.UUID,
    user_id: str,
) -> bool:
    """Delete a session and all its messages. Returns True if found."""
    session = get_session(db, session_id, user_id)
    if not session:
        return False
    db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
    db.delete(session)
    db.flush()
    return True


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------

def add_message(
    db: Session,
    session_id: uuid.UUID,
    user_id: str,
    role: str,
    content: str,
    ui_commands: Optional[list] = None,
    metadata: Optional[dict] = None,
) -> ChatMessage:
    """Persist a single chat message."""
    msg = ChatMessage(
        id=uuid.uuid4(),
        session_id=session_id,
        user_id=user_id,
        role=role,
        content=content,
        ui_commands=ui_commands,
        metadata_=metadata,
        created_at=_utcnow(),
    )
    db.add(msg)
    # Touch session updated_at
    db.query(ChatSession).filter(ChatSession.id == session_id).update(
        {"updated_at": _utcnow()}
    )
    db.flush()
    return msg


def get_messages(
    db: Session,
    session_id: uuid.UUID,
    user_id: str,
    skip: int = 0,
    limit: int = 200,
) -> List[ChatMessage]:
    """Get messages for a session, oldest first."""
    return (
        db.query(ChatMessage)
        .filter(
            ChatMessage.session_id == session_id,
            ChatMessage.user_id == user_id,
        )
        .order_by(ChatMessage.created_at.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def auto_title_from_content(content: str, max_len: int = 60) -> str:
    """Generate a short session title from the first user message."""
    title = content.strip().replace("\n", " ")
    if len(title) > max_len:
        title = title[: max_len - 3] + "..."
    return title


# ---------------------------------------------------------------------------
# Intent recognition & UI command generation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Headcount NL parsing — entity extraction
# ---------------------------------------------------------------------------

# Common fiber/data center role names for entity extraction
KNOWN_ROLES = [
    "fiber splicer", "cable puller", "otdr tester", "lead splicer",
    "site supervisor", "project lead", "field technician", "data center tech",
    "structured cabling tech", "aerial tech", "underground tech",
    "network engineer", "quality inspector", "safety officer",
    "splicer", "puller", "technician", "tech", "lead", "supervisor",
    "installer", "tester",
]

# Regions / locations commonly referenced
KNOWN_REGIONS = [
    "texas", "california", "florida", "new york", "georgia",
    "north carolina", "virginia", "ohio", "illinois", "arizona",
    "southeast", "northeast", "midwest", "southwest", "northwest",
    "pacific northwest", "mid-atlantic", "gulf coast",
    "austin", "dallas", "houston", "san antonio", "phoenix",
    "atlanta", "charlotte", "denver", "portland", "seattle",
    "chicago", "miami", "orlando", "tampa", "los angeles",
    "san francisco", "san diego", "raleigh", "richmond",
]

# Number words
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "a": 1, "an": 1, "a couple": 2, "a few": 3, "several": 4,
    "half a dozen": 6, "a dozen": 12,
}

# Headcount intent patterns (checked before general intents)
HEADCOUNT_PATTERNS = [
    # "I need 3 fiber splicers in Austin"
    r"(?:i\s+)?need\s+(\d+|(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:more\s+)?)?(.+?)(?:\s+(?:in|for|at|near)\s+(.+?))?$",
    # "request 5 technicians for the Austin project"
    r"(?:request|add|hire|staff|get\s+me|bring\s+on|onboard)\s+(\d+|(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:more\s+)?)?(.+?)(?:\s+(?:in|for|at|near)\s+(.+?))?$",
    # "can we get 3 more splicers?"
    r"(?:can\s+(?:we|i|you)\s+(?:get|add|bring|hire|request))\s+(\d+|(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:more\s+)?)?(.+?)(?:\s+(?:in|for|at|near)\s+(.+?))?$",
    # "we need more fiber splicers for dallas"
    r"we\s+need\s+(?:more\s+)?(\d+\s+)?(.+?)(?:\s+(?:in|for|at|near)\s+(.+?))?$",
    # "staff up with 4 cable pullers in houston"
    r"staff\s+up\s+(?:with\s+)?(\d+\s+)?(.+?)(?:\s+(?:in|for|at|near)\s+(.+?))?$",
    # "headcount request for 3 splicers"
    r"headcount\s+(?:request\s+)?(?:for\s+)?(\d+\s+)?(.+?)(?:\s+(?:in|for|at|near)\s+(.+?))?$",
]

# Pattern to detect headcount intent (broader/simpler check before entity extraction)
HEADCOUNT_INTENT_TRIGGERS = [
    r"(?:i\s+)?need\s+\d+\s+(?:more\s+)?(?:\w+\s+)?(?:tech|splicer|puller|lead|installer|tester|supervisor|engineer|inspector|officer|cable|fiber)",
    r"(?:request|add|hire|staff|onboard|bring\s+on|get\s+me)\s+(?:\d+\s+|(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+)?(?:more\s+)?(?:tech|splicer|puller|lead|installer|tester|supervisor|engineer|inspector|officer|cable|fiber)",
    r"(?:can\s+(?:we|i|you)\s+(?:get|add|bring|hire|request))\s+(?:\d+\s+|(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+)?(?:more\s+)?(?:tech|splicer|puller|lead|installer|tester|supervisor|engineer|inspector|officer|cable|fiber)",
    r"we\s+need\s+(?:more\s+)?(?:\d+\s+)?(?:tech|splicer|puller|lead|installer|tester|supervisor|engineer|inspector|officer|cable|fiber)",
    r"(?:staff\s+up|headcount\s+request)",
    r"headcount\s+(?:for|request)",
    r"need\s+(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:more\s+)?(?:tech|splicer|puller|lead|installer|tester|supervisor|engineer|inspector|officer|cable|fiber)",
]


def _extract_count(text: Optional[str]) -> int:
    """Extract numeric count from text. Returns 1 as default."""
    if not text:
        return 1
    text = text.strip().rstrip()
    # Try direct digit
    digit_match = re.search(r"(\d+)", text)
    if digit_match:
        return int(digit_match.group(1))
    # Try word-based
    text_lower = text.lower().strip()
    for word, num in sorted(NUMBER_WORDS.items(), key=lambda x: -len(x[0])):
        if word in text_lower:
            return num
    return 1


def _normalize_role(raw: str) -> Optional[str]:
    """Normalize extracted role name to a known role or return cleaned version."""
    raw = raw.strip().lower()
    # Remove trailing plurals for matching
    raw_singular = re.sub(r"s$", "", raw)
    raw_singular = re.sub(r"ies$", "y", raw_singular)  # e.g. techs -> tech

    # Check against known roles
    for known in KNOWN_ROLES:
        if known in raw or raw in known:
            return known.title()
        known_singular = re.sub(r"s$", "", known)
        if known_singular in raw_singular or raw_singular in known_singular:
            return known.title()

    # If it seems role-like (contains tech/fiber/cable/splicer etc.), return cleaned
    role_keywords = ["tech", "splicer", "puller", "lead", "supervisor", "installer",
                     "tester", "engineer", "inspector", "officer", "cable", "fiber"]
    for kw in role_keywords:
        if kw in raw:
            return raw.strip().title()

    return None


def _normalize_location(raw: str) -> Optional[str]:
    """Normalize extracted location against known regions/cities."""
    raw = raw.strip().lower()
    # Remove trailing words like "project", "area", "region"
    raw = re.sub(r"\s+(project|area|region|office|site)$", "", raw)

    for known in KNOWN_REGIONS:
        if known in raw or raw in known:
            return known.title()

    # Return cleaned version if it looks like a place
    if len(raw) > 1:
        return raw.strip().title()
    return None


def _is_headcount_intent(message: str) -> bool:
    """Quick check if message matches headcount intent triggers."""
    msg_lower = message.lower().strip()
    for pattern in HEADCOUNT_INTENT_TRIGGERS:
        if re.search(pattern, msg_lower):
            return True
    return False


def extract_headcount_entities(message: str) -> Optional[Dict[str, Any]]:
    """Extract headcount entities (role, count, location) from natural language.

    Returns dict with keys: role, count, location (any may be None if not found).
    Returns None if no headcount intent detected.
    """
    if not _is_headcount_intent(message):
        return None

    msg_lower = message.lower().strip()
    entities: Dict[str, Any] = {"role": None, "count": 1, "location": None}

    for pattern in HEADCOUNT_PATTERNS:
        match = re.search(pattern, msg_lower)
        if match:
            count_raw = match.group(1)
            role_raw = match.group(2)
            location_raw = match.group(3) if match.lastindex >= 3 else None

            entities["count"] = _extract_count(count_raw)

            if role_raw:
                role = _normalize_role(role_raw)
                if role:
                    entities["role"] = role

            if location_raw:
                location = _normalize_location(location_raw)
                if location:
                    entities["location"] = location

            break

    # Fallback: try to extract entities directly from the message
    if not entities["role"]:
        for known in KNOWN_ROLES:
            if known in msg_lower or known + "s" in msg_lower:
                entities["role"] = known.title()
                break

    if not entities["location"]:
        for known in KNOWN_REGIONS:
            if known in msg_lower:
                entities["location"] = known.title()
                break

    # Extract count if still default
    if entities["count"] == 1:
        digit_match = re.search(r"(\d+)\s+(?:more\s+)?(?:tech|splicer|puller|lead|installer|tester|supervisor)", msg_lower)
        if digit_match:
            entities["count"] = int(digit_match.group(1))

    return entities


def _find_matching_projects(db: Session, location: Optional[str], partner_id: Optional[str] = None) -> List[Dict]:
    """Find projects matching a location for headcount association."""
    q = db.query(Project).filter(Project.status.in_(["Active", "Staffing"]))
    if location:
        q = q.filter(
            (Project.location_city.ilike(f"%{location}%")) |
            (Project.location_region.ilike(f"%{location}%")) |
            (Project.name.ilike(f"%{location}%"))
        )
    if partner_id:
        try:
            pid = uuid.UUID(partner_id)
            q = q.filter(Project.partner_id == pid)
        except (ValueError, AttributeError):
            pass
    return [{"id": str(p.id), "name": p.name, "region": p.location_region} for p in q.limit(5).all()]


def _find_matching_partner(db: Session, location: Optional[str] = None) -> Optional[Dict]:
    """Find a partner associated with projects in the given location."""
    if not location:
        # Return first partner as default
        p = db.query(Partner).first()
        if p:
            return {"id": str(p.id), "name": p.name}
        return None
    # Find partner via project location
    project = db.query(Project).filter(
        (Project.location_city.ilike(f"%{location}%")) |
        (Project.location_region.ilike(f"%{location}%")) |
        (Project.name.ilike(f"%{location}%"))
    ).first()
    if project and project.partner:
        return {"id": str(project.partner_id), "name": project.partner.name}
    # Fallback to first partner
    p = db.query(Partner).first()
    if p:
        return {"id": str(p.id), "name": p.name}
    return None


# ---------------------------------------------------------------------------
# Headcount confirmation — create PendingHeadcountRequest
# ---------------------------------------------------------------------------

def confirm_headcount_request(
    db: Session,
    user_id: str,
    role_name: str,
    quantity: int,
    location: Optional[str] = None,
    partner_id: Optional[str] = None,
    project_id: Optional[str] = None,
    start_date: Optional[date] = None,
    constraints: Optional[str] = None,
    notes: Optional[str] = None,
) -> PendingHeadcountRequest:
    """Create a PendingHeadcountRequest after user confirmation.

    This is the terminal action of the two-path confirmation flow:
    1. NL confirmation: user confirms extracted entities directly in chat
    2. Structured form fallback: user fills in missing entities via form

    Returns the created PendingHeadcountRequest.
    """
    # Resolve partner
    resolved_partner_id = None
    if partner_id:
        try:
            resolved_partner_id = uuid.UUID(partner_id)
        except ValueError:
            pass
    if not resolved_partner_id:
        partner = _find_matching_partner(db, location)
        if partner:
            resolved_partner_id = uuid.UUID(partner["id"])

    if not resolved_partner_id:
        raise ValueError("No partner found. Please specify a partner or project.")

    # Resolve project
    resolved_project_id = None
    if project_id:
        try:
            resolved_project_id = uuid.UUID(project_id)
        except ValueError:
            pass

    # Default start date to 2 weeks from now
    if not start_date:
        start_date = (datetime.now(timezone.utc) + timedelta(days=14)).date()

    headcount = PendingHeadcountRequest(
        id=uuid.uuid4(),
        partner_id=resolved_partner_id,
        project_id=resolved_project_id,
        role_name=role_name,
        quantity=quantity,
        priority="normal",
        start_date=start_date,
        constraints=constraints,
        notes=notes or f"Created via chat by {user_id}",
        status=HeadcountRequestStatus.PENDING.value,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(headcount)
    db.flush()
    return headcount


# ---------------------------------------------------------------------------
# Headcount response generation
# ---------------------------------------------------------------------------

def generate_headcount_response(
    db: Session,
    entities: Dict[str, Any],
    user_role: str,
) -> Tuple[str, list[dict], Dict[str, Any]]:
    """Generate response for headcount intent with two-path confirmation.

    Path 1 (direct NL confirmation): All required entities extracted
    → Present summary and ask for confirmation in chat

    Path 2 (structured form fallback): Missing entities
    → Return partial data with form prompt

    Returns: (response_text, ui_commands, metadata_with_entities)
    """
    role = entities.get("role")
    count = entities.get("count", 1)
    location = entities.get("location")

    # Determine completeness
    has_role = role is not None
    has_count = count is not None and count > 0
    has_location = location is not None

    ui_commands: list[dict] = []
    metadata: Dict[str, Any] = {
        "intent": "headcount_request",
        "entities": entities,
        "confirmation_required": True,
    }

    # Find matching projects for context
    matching_projects = _find_matching_projects(db, location) if location else []
    partner = _find_matching_partner(db, location)

    if matching_projects:
        metadata["matching_projects"] = matching_projects
    if partner:
        metadata["partner"] = partner

    if has_role and has_count:
        # PATH 1: Direct NL confirmation — all key entities present
        location_str = f" in **{location}**" if location else ""
        project_str = ""
        if matching_projects:
            project_names = ", ".join(p["name"] for p in matching_projects[:3])
            project_str = f"\n- **Matching projects:** {project_names}"

        partner_str = ""
        if partner:
            partner_str = f"\n- **Partner:** {partner['name']}"

        response = (
            f"I'll create a headcount request with these details:\n\n"
            f"- **Role:** {role}\n"
            f"- **Quantity:** {count}\n"
            f"- **Location:** {location or 'Not specified'}"
            f"{partner_str}"
            f"{project_str}\n"
            f"- **Start date:** ~2 weeks from now\n\n"
            f"**Would you like to confirm this request?** "
            f"Reply \"yes\" or \"confirm\" to submit, or \"edit\" to modify the details."
        )

        metadata["confirmation_path"] = "direct_nl"
        metadata["ready_to_confirm"] = True

        # Add a toast command for visual feedback
        ui_commands.append({
            "action": "toast",
            "target": "info",
            "params": {"message": f"Headcount request: {count} {role}{location_str}"},
            "label": "Headcount Request Preview",
        })

    else:
        # PATH 2: Structured form fallback — missing entities
        missing = []
        if not has_role:
            missing.append("role/position")
        if not has_count or count <= 0:
            missing.append("quantity")

        known_parts = []
        if has_role:
            known_parts.append(f"**Role:** {role}")
        if has_count and count > 0:
            known_parts.append(f"**Quantity:** {count}")
        if has_location:
            known_parts.append(f"**Location:** {location}")

        known_str = "\n- ".join(known_parts) if known_parts else "None extracted"

        response = (
            f"I'd like to help create a headcount request. Here's what I extracted:\n\n"
            f"- {known_str}\n\n"
            f"I still need: **{', '.join(missing)}**.\n\n"
            f"You can:\n"
            f"1. **Tell me the missing details** — e.g., \"I need 3 fiber splicers in Austin\"\n"
            f"2. **Use the headcount form** — I'll open it with what I have pre-filled\n\n"
            f"Which would you prefer?"
        )

        metadata["confirmation_path"] = "form_fallback"
        metadata["ready_to_confirm"] = False
        metadata["missing_fields"] = missing

        # Navigate to projects with a headcount form hint
        ui_commands.append({
            "action": "toast",
            "target": "info",
            "params": {"message": "Some details needed for headcount request"},
            "label": "Incomplete Request",
        })

    return response, ui_commands, metadata


# ---------------------------------------------------------------------------
# UI State context builder — turns client UI state into agent-readable context
# ---------------------------------------------------------------------------

# Human-friendly screen names for route paths
ROUTE_LABELS = {
    "/ops/dashboard": "Operations Dashboard",
    "/ops/technicians": "Technician Directory",
    "/ops/projects": "Project Staffing",
    "/ops/training": "Training Pipeline",
    "/ops/inbox": "Agent Inbox",
    "/tech/portal": "Technician Portal",
    "/partner/portal": "Partner Portal",
}


def _build_ui_context_summary(ui_state: Optional[Dict[str, Any]]) -> Optional[str]:
    """Build a human-readable summary of the user's current UI state.

    This is injected into the LangChain agent's tool context so responses
    are aware of what the user is currently viewing.

    Returns None if no meaningful UI state is provided.
    """
    if not ui_state:
        return None

    parts: List[str] = []

    # Current screen
    route = ui_state.get("current_route")
    if route:
        label = ROUTE_LABELS.get(route, route)
        parts.append(f"Current screen: {label} ({route})")

    # Active filters
    filters = ui_state.get("active_filters") or {}
    if filters:
        filter_strs = [f"{k}={v}" for k, v in filters.items() if v]
        if filter_strs:
            parts.append(f"Active filters: {', '.join(filter_strs)}")

    # Active tab
    tab = ui_state.get("active_tab")
    if tab:
        parts.append(f"Active tab: {tab}")

    # Selected entity
    entity_id = ui_state.get("selected_entity_id")
    entity_type = ui_state.get("selected_entity_type")
    if entity_id and entity_type:
        parts.append(f"Viewing {entity_type} ID: {entity_id}")
    elif entity_id:
        parts.append(f"Selected entity ID: {entity_id}")

    # Viewport
    viewport = ui_state.get("viewport")
    if viewport:
        parts.append(f"Device: {viewport}")

    return " | ".join(parts) if parts else None


def _is_user_already_on_screen(ui_state: Optional[Dict[str, Any]], target_route: str) -> bool:
    """Check if the user is already on the target screen."""
    if not ui_state:
        return False
    return ui_state.get("current_route", "").rstrip("/") == target_route.rstrip("/")


def _get_active_filter_value(ui_state: Optional[Dict[str, Any]], key: str) -> Optional[str]:
    """Get the value of an active filter from UI state."""
    if not ui_state:
        return None
    filters = ui_state.get("active_filters") or {}
    return filters.get(key)


INTENT_PATTERNS = [
    # Greeting / help — check first since they're short, distinct patterns
    (r"^(?:hi|hello|hey|good\s+(?:morning|afternoon|evening))", "greeting"),
    (r"^(?:help|what\s+can\s+you\s+do|commands|menu)", "help"),

    # Incremental filter intents — additive/subtractive (checked before full filter intents)
    (r"(?:also|additionally|and\s+also|narrow)\s+(?:filter|show|include|add)\s+(?:by\s+)?(?:ready\s+now|ready)", "add_filter_ready_now"),
    (r"(?:also|additionally|and\s+also|narrow)\s+(?:filter|show|include|add)\s+(?:by\s+)?in\s+training", "add_filter_in_training"),
    (r"(?:also|additionally|and\s+also|narrow)\s+(?:filter|show|include|add)\s+(?:by\s+)?(?:skill|skilled\s+in)\s+(.+)", "add_filter_skill"),
    (r"(?:also|additionally|and\s+also|narrow)\s+(?:filter|show|include|add)\s+(?:by\s+)?(?:region\s+)?(?:in\s+)?(\w[\w\s]*?)$", "add_filter_region"),
    (r"(?:also|additionally|and\s+also|narrow)\s+(?:filter|show|include|add)\s+(?:by\s+)?(.+)", "add_filter_generic"),
    (r"(?:remove|drop|delete|stop\s+filtering)\s+(?:the\s+)?(?:region)\s+filter", "remove_filter_region"),
    (r"(?:remove|drop|delete|stop\s+filtering)\s+(?:the\s+)?(?:skill)\s+filter", "remove_filter_skill"),
    (r"(?:remove|drop|delete|stop\s+filtering)\s+(?:the\s+)?(?:status|deployability)\s+filter", "remove_filter_status"),
    (r"(?:remove|drop|delete|stop\s+filtering)\s+(?:the\s+)?(?:career|stage)\s+filter", "remove_filter_stage"),
    (r"(?:remove|drop|delete|stop\s+filtering)\s+(?:the\s+)?(?:search|name)\s+filter", "remove_filter_search"),

    # Filter intents — BEFORE navigation so "show techs in training" matches filter, not nav
    (r"(?:filter|show|find|list|search)\s+(?:me\s+)?(?:technicians?\s+)?(?:who\s+are\s+|that\s+are\s+|with\s+status\s+)?(?:ready\s+now|ready)", "filter_ready_now"),
    (r"(?:filter|show|find|list|search)\s+(?:me\s+)?(?:technicians?\s+)?(?:who\s+are\s+|that\s+are\s+)?in\s+training", "filter_in_training"),
    (r"(?:filter|show|find|list|search)\s+(?:me\s+)?(?:technicians?\s+)?(?:who\s+are\s+|that\s+are\s+)?(?:currently\s+)?(?:deployed|assigned)", "filter_deployed"),
    (r"(?:filter|show|find|list|search)\s+(?:me\s+)?(?:technicians?\s+)?(?:who\s+have\s+|with\s+)?missing\s+(?:cert|certification)s?", "filter_missing_cert"),
    (r"(?:filter|show|find|list|search)\s+(?:me\s+)?(?:technicians?\s+)?(?:who\s+have\s+|with\s+)?missing\s+doc(?:ument)?s?", "filter_missing_docs"),
    (r"(?:filter|show|find|list|search)\s+(?:me\s+)?(?:technicians?\s+)?(?:who\s+are\s+)?rolling\s+off", "filter_rolling_off"),
    (r"(?:filter|show|find|list|search)\s+(?:me\s+)?(?:technicians?\s+)?(?:in|from|near)\s+(\w[\w\s]*?)(?:\s+region)?$", "filter_by_region"),
    (r"(?:filter|show|find|list|search)\s+(?:me\s+)?(?:technicians?\s+)?(?:with\s+)?(?:skill|skilled\s+in)\s+(.+)", "filter_by_skill"),
    (r"(?:search|find)\s+(?:for\s+)?[\"']?(\w[\w\s]*?)[\"']?$", "search_term"),

    # Navigation intents — after filter patterns
    (r"(?:show|open|go\s+to|navigate\s+to|view|take\s+me\s+to)\s+(?:the\s+)?dashboard", "navigate_dashboard"),
    (r"(?:show|open|go\s+to|navigate\s+to|view)\s+(?:the\s+)?technician(?:s)?(?:\s+directory|\s+list)?$", "navigate_technicians"),
    (r"(?:show|open|go\s+to|navigate\s+to|view)\s+(?:the\s+)?training(?:\s+pipeline)?", "navigate_training"),
    (r"(?:show|open|go\s+to|navigate\s+to|view)\s+(?:the\s+)?project(?:s)?(?:\s+staffing)?", "navigate_projects"),
    (r"(?:show|open|go\s+to|navigate\s+to|view)\s+(?:the\s+)?(?:agent\s+)?inbox", "navigate_inbox"),
    (r"(?:show|open|go\s+to|navigate\s+to|view)\s+(?:the\s+)?(?:tech(?:nician)?\s+)?portal", "navigate_portal"),

    # Query intents
    (r"how\s+many\s+technicians?\s+(?:are\s+)?ready", "query_ready_count"),
    (r"how\s+many\s+technicians?", "query_tech_count"),
    (r"(?:what|which)\s+projects?\s+(?:are\s+)?(?:currently\s+)?(?:active|staffing)", "query_active_projects"),
    (r"(?:who|which\s+tech)\s+(?:is|are)\s+(?:available|free|ready)", "query_available_techs"),
    (r"(?:what|show)\s+(?:are\s+)?(?:the\s+)?pending\s+(?:recommendations?|actions?)", "query_pending_recs"),

]


def parse_intent(message: str) -> Tuple[str, Optional[str]]:
    """Parse user message to determine intent and extract parameters.

    Headcount intents are checked first, then standard patterns.
    Confirmation responses ("yes", "confirm") are checked in context.
    """
    msg_lower = message.lower().strip()

    # Check for headcount confirmation responses
    if msg_lower in ("yes", "confirm", "yes please", "do it", "submit", "go ahead",
                      "confirmed", "approve", "yep", "yeah", "sure", "ok", "okay"):
        return "headcount_confirm", None

    # Check for edit/modify responses (form fallback path)
    if msg_lower in ("edit", "modify", "change", "update", "form", "use form",
                      "open form", "use the form", "open the form"):
        return "headcount_edit", None

    # Check headcount intent before standard patterns
    if _is_headcount_intent(message):
        return "headcount_request", None

    for pattern, intent in INTENT_PATTERNS:
        match = re.search(pattern, msg_lower)
        if match:
            param = match.group(1) if match.lastindex and match.lastindex >= 1 else None
            return intent, param
    return "general", None


def generate_response(
    intent: str,
    param: Optional[str],
    user_role: str,
    current_ui_state: Optional[Dict[str, Any]] = None,
) -> Tuple[str, list[dict]]:
    """Generate a response and UI commands based on parsed intent.

    When ``current_ui_state`` is provided the response is enriched with
    context-aware language (e.g. "I see you're already on the Technician
    Directory — let me just adjust the filters") and redundant navigation
    commands are suppressed.
    """
    ui_commands: list[dict] = []
    response = ""
    ui_ctx = _build_ui_context_summary(current_ui_state)

    if intent == "greeting":
        response = (
            "Hey! I'm the Deployable assistant. I can help you navigate the platform, "
            "filter technicians, check project status, and more. Just ask me anything \u2014 "
            "for example, \"show me ready technicians\" or \"open the training pipeline\"."
        )

    elif intent == "help":
        response = (
            "Here's what I can help with:\n\n"
            "**Navigation:**\n"
            "- \"Open the dashboard\" \u2014 Jump to any screen\n"
            "- \"Show projects\" \u2014 Open the projects view\n\n"
            "**Filtering:**\n"
            "- \"Show ready technicians\" \u2014 Filter by status\n"
            "- \"Find techs in Southeast\" \u2014 Filter by region\n"
            "- \"Filter by OTDR skill\" \u2014 Filter by skill\n\n"
            "**Queries:**\n"
            "- \"How many techs are ready?\" \u2014 Quick counts\n"
            "- \"What projects are staffing?\" \u2014 Status checks\n"
            "- \"Who is available?\" \u2014 Availability queries\n\n"
            "Just type naturally and I'll figure out what you need!"
        )

    elif intent == "navigate_dashboard":
        if _is_user_already_on_screen(current_ui_state, "/ops/dashboard"):
            response = "You're already on the **Dashboard**. What would you like to know about the current metrics?"
        else:
            response = "Opening the **Dashboard** for you."
            ui_commands = [{"action": "navigate", "target": "/ops/dashboard", "label": "Open Dashboard"}]

    elif intent == "navigate_technicians":
        if _is_user_already_on_screen(current_ui_state, "/ops/technicians"):
            active_filters = (current_ui_state or {}).get("active_filters") or {}
            if active_filters:
                filter_desc = ", ".join(f"{k}: {v}" for k, v in active_filters.items())
                response = f"You're already on the **Technician Directory** with filters: {filter_desc}. Want me to adjust the filters or clear them?"
            else:
                response = "You're already on the **Technician Directory**. Want me to filter by status, region, or skill?"
        else:
            response = "Here's the **Technician Directory**."
            ui_commands = [{"action": "navigate", "target": "/ops/technicians", "label": "Open Technician Directory"}]

    elif intent == "navigate_training":
        if _is_user_already_on_screen(current_ui_state, "/ops/training"):
            response = "You're already viewing the **Training Pipeline**. I can help filter by stage or search for a technician."
        else:
            response = "Opening the **Training Pipeline**."
            ui_commands = [{"action": "navigate", "target": "/ops/training", "label": "Open Training Pipeline"}]

    elif intent == "navigate_projects":
        if _is_user_already_on_screen(current_ui_state, "/ops/projects"):
            response = "You're already on **Project Staffing**. Want me to filter by status or search for a project?"
        else:
            response = "Here are your **Projects**."
            ui_commands = [{"action": "navigate", "target": "/ops/projects", "label": "Open Projects"}]

    elif intent == "navigate_inbox":
        if _is_user_already_on_screen(current_ui_state, "/ops/inbox"):
            active_tab = _get_active_filter_value(current_ui_state, "tab") or (current_ui_state or {}).get("active_tab")
            if active_tab:
                response = f"You're already in the **Agent Inbox** on the **{active_tab}** tab. Want me to switch tabs or filter?"
            else:
                response = "You're already in the **Agent Inbox**. I can switch tabs — try 'show preference rules' or 'show activity log'."
        else:
            response = "Opening the **Agent Inbox**."
            ui_commands = [{"action": "navigate", "target": "/ops/inbox", "label": "Open Agent Inbox"}]

    elif intent == "navigate_portal":
        if _is_user_already_on_screen(current_ui_state, "/tech/portal"):
            response = "You're already on the **Technician Portal**. What do you need help with?"
        else:
            response = "Opening the **Technician Portal**."
            ui_commands = [{"action": "navigate", "target": "/tech/portal", "label": "Open Technician Portal"}]

    # ── Incremental filter handlers (add_filter / remove_filter) ────────────
    elif intent == "add_filter_ready_now":
        response = "Adding **Ready Now** status filter to your current view. Existing filters are preserved."
        ui_commands = [
            {"action": "add_filter", "target": "/ops/technicians", "params": {"status": "Ready Now"}, "label": "Add: Ready Now"},
        ]

    elif intent == "add_filter_in_training":
        response = "Adding **In Training** status filter. Existing filters are preserved."
        ui_commands = [
            {"action": "add_filter", "target": "/ops/technicians", "params": {"status": "In Training"}, "label": "Add: In Training"},
        ]

    elif intent == "add_filter_skill":
        skill = param.strip().title() if param else "Unknown"
        response = f"Adding **{skill}** skill filter. Existing filters are preserved."
        ui_commands = [
            {"action": "add_filter", "target": "/ops/technicians", "params": {"skill": skill}, "label": f"Add: {skill} skill"},
        ]

    elif intent == "add_filter_region":
        region = param.strip().title() if param else "Unknown"
        response = f"Adding **{region}** region filter. Existing filters are preserved."
        ui_commands = [
            {"action": "add_filter", "target": "/ops/technicians", "params": {"region": region}, "label": f"Add: {region} region"},
        ]

    elif intent == "add_filter_generic":
        filter_val = param.strip() if param else "Unknown"
        response = f"Adding filter for **{filter_val}**. Existing filters are preserved."
        ui_commands = [
            {"action": "add_filter", "target": "/ops/technicians", "params": {"search": filter_val}, "label": f"Add: {filter_val}"},
        ]

    elif intent == "remove_filter_region":
        response = "Removed the **region** filter. Other filters remain active."
        ui_commands = [
            {"action": "remove_filter", "target": "/ops/technicians", "params": {"region": ""}, "label": "Remove: region"},
        ]

    elif intent == "remove_filter_skill":
        response = "Removed the **skill** filter. Other filters remain active."
        ui_commands = [
            {"action": "remove_filter", "target": "/ops/technicians", "params": {"skill": ""}, "label": "Remove: skill"},
        ]

    elif intent == "remove_filter_status":
        response = "Removed the **status** filter. Other filters remain active."
        ui_commands = [
            {"action": "remove_filter", "target": "/ops/technicians", "params": {"status": ""}, "label": "Remove: status"},
        ]

    elif intent == "remove_filter_stage":
        response = "Removed the **career stage** filter. Other filters remain active."
        ui_commands = [
            {"action": "remove_filter", "target": "/ops/technicians", "params": {"career_stage": ""}, "label": "Remove: career stage"},
        ]

    elif intent == "remove_filter_search":
        response = "Removed the **search** filter. Other filters remain active."
        ui_commands = [
            {"action": "remove_filter", "target": "/ops/technicians", "params": {"search": ""}, "label": "Remove: search"},
        ]

    elif intent == "filter_ready_now":
        already_on_tech = _is_user_already_on_screen(current_ui_state, "/ops/technicians")
        current_status = _get_active_filter_value(current_ui_state, "status")
        if already_on_tech and current_status == "Ready Now":
            response = "You're already viewing **Ready Now** technicians. Want me to add more filters (region, skill) or clear them?"
        elif already_on_tech:
            response = "Adjusting filters to show **Ready Now** technicians \u2014 fully certified, documented, and available."
            ui_commands = [
                {"action": "filter", "target": "/ops/technicians", "params": {"status": "Ready Now"}, "label": "Filter: Ready Now"},
            ]
        else:
            response = "Filtering to show **Ready Now** technicians. These are fully certified, documented, and available for assignment."
            ui_commands = [
                {"action": "navigate", "target": "/ops/technicians", "params": {"status": "Ready Now"}, "label": "View Ready Now technicians"},
                {"action": "filter", "target": "/ops/technicians", "params": {"status": "Ready Now"}, "label": "Filter: Ready Now"},
            ]

    elif intent == "filter_in_training":
        already_on_tech = _is_user_already_on_screen(current_ui_state, "/ops/technicians")
        if already_on_tech:
            response = "Switching filter to **In Training** \u2014 techs actively building their skills."
            ui_commands = [
                {"action": "filter", "target": "/ops/technicians", "params": {"status": "In Training"}, "label": "Filter: In Training"},
            ]
        else:
            response = "Showing technicians that are **In Training**. These techs are actively building their skills."
            ui_commands = [
                {"action": "navigate", "target": "/ops/technicians", "params": {"status": "In Training"}, "label": "View In Training technicians"},
                {"action": "filter", "target": "/ops/technicians", "params": {"status": "In Training"}, "label": "Filter: In Training"},
            ]

    elif intent == "filter_deployed":
        already_on_tech = _is_user_already_on_screen(current_ui_state, "/ops/technicians")
        if already_on_tech:
            response = "Switching filter to **Currently Assigned** \u2014 the ones out in the field right now."
            ui_commands = [
                {"action": "filter", "target": "/ops/technicians", "params": {"status": "Currently Assigned"}, "label": "Filter: Currently Assigned"},
            ]
        else:
            response = "Showing **Currently Assigned** technicians \u2014 the ones out in the field right now."
            ui_commands = [
                {"action": "navigate", "target": "/ops/technicians", "params": {"status": "Currently Assigned"}, "label": "View assigned technicians"},
                {"action": "filter", "target": "/ops/technicians", "params": {"status": "Currently Assigned"}, "label": "Filter: Currently Assigned"},
            ]

    elif intent == "filter_missing_cert":
        already_on_tech = _is_user_already_on_screen(current_ui_state, "/ops/technicians")
        if already_on_tech:
            response = "Switching filter to **Missing Certifications** \u2014 these need attention before deployment."
            ui_commands = [
                {"action": "filter", "target": "/ops/technicians", "params": {"status": "Missing Cert"}, "label": "Filter: Missing Cert"},
            ]
        else:
            response = "Filtering for technicians with **Missing Certifications**. These need attention before deployment."
            ui_commands = [
                {"action": "navigate", "target": "/ops/technicians", "params": {"status": "Missing Cert"}, "label": "View Missing Cert technicians"},
                {"action": "filter", "target": "/ops/technicians", "params": {"status": "Missing Cert"}, "label": "Filter: Missing Cert"},
            ]

    elif intent == "filter_missing_docs":
        already_on_tech = _is_user_already_on_screen(current_ui_state, "/ops/technicians")
        if already_on_tech:
            response = "Switching filter to **Missing Documents** \u2014 these need doc verification."
            ui_commands = [
                {"action": "filter", "target": "/ops/technicians", "params": {"status": "Missing Docs"}, "label": "Filter: Missing Docs"},
            ]
        else:
            response = "Filtering for technicians with **Missing Documents**. These need doc verification."
            ui_commands = [
                {"action": "navigate", "target": "/ops/technicians", "params": {"status": "Missing Docs"}, "label": "View Missing Docs technicians"},
                {"action": "filter", "target": "/ops/technicians", "params": {"status": "Missing Docs"}, "label": "Filter: Missing Docs"},
            ]

    elif intent == "filter_rolling_off":
        already_on_tech = _is_user_already_on_screen(current_ui_state, "/ops/technicians")
        if already_on_tech:
            response = "Switching filter to **Rolling Off Soon** \u2014 they'll be available for new assignments."
            ui_commands = [
                {"action": "filter", "target": "/ops/technicians", "params": {"status": "Rolling Off Soon"}, "label": "Filter: Rolling Off Soon"},
            ]
        else:
            response = "Showing technicians that are **Rolling Off Soon** \u2014 they'll be available for new assignments."
            ui_commands = [
                {"action": "navigate", "target": "/ops/technicians", "params": {"status": "Rolling Off Soon"}, "label": "View Rolling Off technicians"},
                {"action": "filter", "target": "/ops/technicians", "params": {"status": "Rolling Off Soon"}, "label": "Filter: Rolling Off Soon"},
            ]

    elif intent == "filter_by_region":
        region = param.strip().title() if param else "Unknown"
        already_on_tech = _is_user_already_on_screen(current_ui_state, "/ops/technicians")
        current_region = _get_active_filter_value(current_ui_state, "region")
        if already_on_tech and current_region and current_region.lower() == region.lower():
            response = f"You're already filtering by the **{region}** region. Want to add more filters?"
        elif already_on_tech:
            response = f"Updating region filter to **{region}**."
            ui_commands = [
                {"action": "filter", "target": "/ops/technicians", "params": {"region": region}, "label": f"Filter: {region} region"},
            ]
        else:
            response = f"Filtering technicians in the **{region}** region."
            ui_commands = [
                {"action": "navigate", "target": "/ops/technicians", "params": {"region": region}, "label": f"View {region} technicians"},
                {"action": "filter", "target": "/ops/technicians", "params": {"region": region}, "label": f"Filter: {region} region"},
            ]

    elif intent == "filter_by_skill":
        skill = param.strip().title() if param else "Unknown"
        already_on_tech = _is_user_already_on_screen(current_ui_state, "/ops/technicians")
        if already_on_tech:
            response = f"Adding skill filter for **{skill}** in the current view."
            ui_commands = [
                {"action": "filter", "target": "/ops/technicians", "params": {"search": skill}, "label": f"Search: {skill}"},
            ]
        else:
            response = f"Searching for technicians skilled in **{skill}**."
            ui_commands = [
                {"action": "navigate", "target": "/ops/technicians", "params": {"search": skill}, "label": f"Search: {skill}"},
                {"action": "filter", "target": "/ops/technicians", "params": {"search": skill}, "label": f"Search: {skill}"},
            ]

    elif intent == "search_term":
        term = param.strip() if param else ""
        already_on_tech = _is_user_already_on_screen(current_ui_state, "/ops/technicians")
        if already_on_tech:
            response = f"Searching for **{term}** in the current view..."
            ui_commands = [
                {"action": "filter", "target": "/ops/technicians", "params": {"search": term}, "label": f"Search: {term}"},
            ]
        else:
            response = f"Searching for **{term}**..."
            ui_commands = [
                {"action": "navigate", "target": "/ops/technicians", "params": {"search": term}, "label": f"Search: {term}"},
                {"action": "filter", "target": "/ops/technicians", "params": {"search": term}, "label": f"Search: {term}"},
            ]

    elif intent == "query_ready_count":
        response = (
            "Based on current data, there are approximately **12 technicians** in Ready Now status. "
            "Would you like me to show them?"
        )
        ui_commands = [
            {"action": "filter", "target": "/ops/technicians", "params": {"status": "Ready Now"}, "label": "View Ready Technicians"},
        ]

    elif intent == "query_tech_count":
        response = (
            "We currently have **55 technicians** in the system across all career stages \u2014 "
            "from Sourced through Deployed. Would you like me to break that down by status?"
        )

    elif intent == "query_active_projects":
        response = (
            "I see several active projects in the system. Let me open the **Projects** view for you."
        )
        ui_commands = [
            {"action": "navigate", "target": "/ops/projects", "label": "Open Projects"},
        ]

    elif intent == "query_available_techs":
        response = (
            "Let me show you technicians who are **Ready Now** \u2014 fully certified and available for assignment."
        )
        ui_commands = [
            {"action": "navigate", "target": "/ops/technicians", "params": {"status": "Ready Now"}, "label": "View available technicians"},
            {"action": "filter", "target": "/ops/technicians", "params": {"status": "Ready Now"}, "label": "Filter: Ready Now"},
        ]

    elif intent == "query_pending_recs":
        response = "Opening the **Agent Inbox** where you can review pending recommendations."
        ui_commands = [
            {"action": "navigate", "target": "/ops/inbox", "label": "Open Agent Inbox"},
        ]

    else:
        response = (
            "I understand you're asking about something. Let me help! I can:\n\n"
            "- **Navigate** to any screen (dashboard, technicians, projects, training, inbox)\n"
            "- **Filter** technicians by status, region, or skill\n"
            "- **Answer questions** about technician counts and availability\n\n"
            "Could you rephrase your request, or try something like \"show ready technicians\"?"
        )

    # Role-scoped safety: partners can't see internal ops data
    if user_role == "partner":
        ui_commands = [cmd for cmd in ui_commands if not cmd.get("target", "").startswith("/ops/")]
        if not ui_commands and intent.startswith("navigate_") and "portal" not in intent:
            response = "That view is restricted to operations users. I can show you your project status instead."

    return response, ui_commands


# ---------------------------------------------------------------------------
# Synchronous send (non-streaming)
# ---------------------------------------------------------------------------

def _get_pending_headcount_context(db: Session, session_id: uuid.UUID, user_id: str) -> Optional[Dict[str, Any]]:
    """Check recent messages for pending headcount confirmation context."""
    recent_msgs = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.session_id == session_id,
            ChatMessage.user_id == user_id,
            ChatMessage.role == "assistant",
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(5)
        .all()
    )
    for msg in recent_msgs:
        meta = msg.metadata_
        if isinstance(meta, dict) and meta.get("intent") == "headcount_request":
            if meta.get("confirmation_required") and meta.get("ready_to_confirm"):
                return meta
    return None


def send_user_message(
    db: Session,
    user_id: str,
    content: str,
    session_id: Optional[uuid.UUID] = None,
    user_role: str = "ops",
    current_ui_state: Optional[Dict[str, Any]] = None,
) -> Tuple[ChatMessage, ChatMessage, ChatSession]:
    """
    Handle a user sending a message:
    1. Create or find session
    2. Persist user message
    3. Generate assistant reply with intent parsing
    4. For headcount requests: entity extraction → two-path confirmation
    5. Return (user_msg, assistant_msg, session)

    ``current_ui_state``, when provided, is a dict snapshot of the user's
    current screen, active filters, active tab, etc.  It is passed into
    ``generate_response`` so the agent can give context-aware, filter-aware
    replies (e.g. skipping navigation if user is already on the right screen).
    """
    if session_id:
        session = get_session(db, session_id, user_id)
        if not session:
            raise ValueError(f"Session {session_id} not found for user {user_id}")
    else:
        title = auto_title_from_content(content)
        session = create_session(db, user_id, title=title)

    user_msg = add_message(
        db=db,
        session_id=session.id,
        user_id=user_id,
        role="user",
        content=content,
    )

    # Parse intent and generate response with UI commands
    intent, param = parse_intent(content)

    if intent == "headcount_request":
        # Extract entities from NL
        entities = extract_headcount_entities(content) or {"role": None, "count": 1, "location": None}
        response_text, ui_commands, metadata = generate_headcount_response(db, entities, user_role)

        assistant_msg = add_message(
            db=db,
            session_id=session.id,
            user_id=user_id,
            role="assistant",
            content=response_text,
            ui_commands=ui_commands if ui_commands else None,
            metadata={
                "model": "deployable-chat",
                "agent": "chat-assistant",
                **metadata,
            },
        )

    elif intent == "headcount_confirm":
        # Check for pending headcount context in this session
        pending_ctx = _get_pending_headcount_context(db, session.id, user_id)

        if pending_ctx and pending_ctx.get("entities"):
            entities = pending_ctx["entities"]
            partner = pending_ctx.get("partner")
            matching_projects = pending_ctx.get("matching_projects", [])

            try:
                headcount = confirm_headcount_request(
                    db=db,
                    user_id=user_id,
                    role_name=entities.get("role", "Technician"),
                    quantity=entities.get("count", 1),
                    location=entities.get("location"),
                    partner_id=partner.get("id") if partner else None,
                    project_id=matching_projects[0]["id"] if matching_projects else None,
                )

                location_str = f" in {entities.get('location')}" if entities.get('location') else ""
                response_text = (
                    f"Headcount request created successfully!\n\n"
                    f"- **Request ID:** `{str(headcount.id)[:8]}...`\n"
                    f"- **Role:** {headcount.role_name}\n"
                    f"- **Quantity:** {headcount.quantity}\n"
                    f"- **Status:** Pending review{location_str}\n\n"
                    f"The request is now in the **Pending** queue for ops review. "
                    f"You can track it in the Agent Inbox."
                )

                ui_commands = [
                    {"action": "navigate", "target": "/ops/inbox", "label": "Open Agent Inbox"},
                    {"action": "toast", "target": "success",
                     "params": {"message": f"Headcount request created: {headcount.quantity} {headcount.role_name}"},
                     "label": "Request Created"},
                ]

                assistant_msg = add_message(
                    db=db,
                    session_id=session.id,
                    user_id=user_id,
                    role="assistant",
                    content=response_text,
                    ui_commands=ui_commands,
                    metadata={
                        "model": "deployable-chat",
                        "agent": "chat-assistant",
                        "intent": "headcount_confirmed",
                        "headcount_request_id": str(headcount.id),
                    },
                )
            except ValueError as e:
                response_text = f"Sorry, I couldn't create the headcount request: {str(e)}"
                assistant_msg = add_message(
                    db=db,
                    session_id=session.id,
                    user_id=user_id,
                    role="assistant",
                    content=response_text,
                    metadata={"model": "deployable-chat", "agent": "chat-assistant",
                              "intent": "headcount_confirm_failed", "error": str(e)},
                )
        else:
            # No pending context — treat as general confirmation
            response_text, ui_commands = generate_response("general", None, user_role, current_ui_state)
            assistant_msg = add_message(
                db=db,
                session_id=session.id,
                user_id=user_id,
                role="assistant",
                content=response_text,
                ui_commands=ui_commands if ui_commands else None,
                metadata={"model": "deployable-chat", "agent": "chat-assistant",
                          "intent": "general", "param": None},
            )

    elif intent == "headcount_edit":
        # User wants the structured form
        pending_ctx = _get_pending_headcount_context(db, session.id, user_id)
        entities = pending_ctx.get("entities", {}) if pending_ctx else {}

        response_text = (
            "Opening the headcount request form for you. "
            "I've pre-filled what I could from your request."
        )
        ui_commands = [
            {"action": "navigate", "target": "/ops/projects", "label": "Open Projects"},
            {"action": "toast", "target": "info",
             "params": {"message": "Use the headcount form to complete your request"},
             "label": "Open Form"},
        ]

        metadata_out = {
            "model": "deployable-chat",
            "agent": "chat-assistant",
            "intent": "headcount_form_redirect",
            "prefill_data": entities,
        }

        assistant_msg = add_message(
            db=db,
            session_id=session.id,
            user_id=user_id,
            role="assistant",
            content=response_text,
            ui_commands=ui_commands,
            metadata=metadata_out,
        )

    else:
        # Standard intent handling — pass UI state for context-aware responses
        response_text, ui_commands = generate_response(
            intent, param, user_role, current_ui_state=current_ui_state,
        )

        # Include UI context summary in metadata for observability
        ui_ctx_summary = _build_ui_context_summary(current_ui_state)
        msg_metadata: Dict[str, Any] = {
            "model": "deployable-chat",
            "agent": "chat-assistant",
            "intent": intent,
            "param": param,
        }
        if ui_ctx_summary:
            msg_metadata["ui_context"] = ui_ctx_summary

        assistant_msg = add_message(
            db=db,
            session_id=session.id,
            user_id=user_id,
            role="assistant",
            content=response_text,
            ui_commands=ui_commands if ui_commands else None,
            metadata=msg_metadata,
        )

    db.commit()
    return user_msg, assistant_msg, session


# ---------------------------------------------------------------------------
# SSE streaming response generator
# ---------------------------------------------------------------------------

async def _stream_deterministic_response(
    response_text: str,
    ui_commands: list[dict],
    metadata: Dict[str, Any],
) -> AsyncGenerator[str, None]:
    """Yield a pre-computed response as fake word-by-word SSE tokens.

    Used for deterministic intents (headcount, navigation, filters) that
    don't need the LLM, or as a fallback when the Anthropic client is
    unavailable.
    """
    words = response_text.split(" ")
    for i, word in enumerate(words):
        token = word if i == 0 else " " + word
        event_data = json.dumps({"token": token})
        yield f"event: token\ndata: {event_data}\n\n"

    if ui_commands:
        cmd_data = json.dumps({"commands": ui_commands})
        yield f"event: ui_command\ndata: {cmd_data}\n\n"

    done_data = json.dumps({
        "content": response_text,
        "ui_commands": ui_commands,
        "metadata": metadata,
    })
    yield f"event: done\ndata: {done_data}\n\n"


async def _stream_claude_response(
    message: str,
    user_role: str,
    session_messages: list[dict],
    metadata: Dict[str, Any],
    ui_context_summary: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Call Claude claude-3-5-sonnet with real streaming and yield SSE events.

    Tokens are emitted one-by-one as they arrive from the API.
    At the end, the full text is parsed for ```ui_commands``` blocks,
    which are emitted as a separate ui_command event.
    """
    client = _get_async_anthropic_client()
    if client is None:
        # Fallback: generate a deterministic response without LLM
        logger.info("No Anthropic client — falling back to deterministic response")
        fallback_text = (
            "I understand you're asking about something. Let me help! I can:\n\n"
            "- **Navigate** to any screen (dashboard, technicians, projects, training, inbox)\n"
            "- **Filter** technicians by status, region, or skill\n"
            "- **Answer questions** about technician counts and availability\n\n"
            "Could you rephrase your request, or try something like \"show ready technicians\"?"
        )
        async for event in _stream_deterministic_response(fallback_text, [], metadata):
            yield event
        return

    # Build messages for the API call
    api_messages = _build_llm_messages(
        message, session_messages, user_role, ui_context_summary
    )

    full_text = ""
    metadata["model"] = CHAT_MODEL
    metadata["agent"] = "chat-assistant-llm"

    try:
        async with client.messages.stream(
            model=CHAT_MODEL,
            max_tokens=CHAT_MAX_TOKENS,
            temperature=CHAT_TEMPERATURE,
            system=CHAT_SYSTEM_PROMPT,
            messages=api_messages,
        ) as stream:
            async for text in stream.text_stream:
                full_text += text
                event_data = json.dumps({"token": text})
                yield f"event: token\ndata: {event_data}\n\n"

    except Exception as exc:
        logger.error("Claude streaming error: %s", exc, exc_info=True)
        # If we got partial text, use it; otherwise fall back
        if not full_text:
            full_text = (
                "I'm having trouble connecting to my language model right now. "
                "I can still help with navigation and filtering — try commands like "
                "\"show ready technicians\" or \"open the dashboard\"."
            )
            event_data = json.dumps({"token": full_text})
            yield f"event: token\ndata: {event_data}\n\n"

    # Parse ui_commands from the LLM response text
    clean_text, ui_commands = _parse_ui_commands_from_text(full_text)

    # If the LLM emitted commands, send them as a ui_command event
    if ui_commands:
        cmd_data = json.dumps({"commands": ui_commands})
        yield f"event: ui_command\ndata: {cmd_data}\n\n"

    # Send done event with the cleaned content (commands block stripped)
    done_data = json.dumps({
        "content": clean_text,
        "ui_commands": ui_commands,
        "metadata": metadata,
    })
    yield f"event: done\ndata: {done_data}\n\n"


async def generate_streaming_response(
    message: str,
    user_role: str,
    session_messages: list[dict],
    db: Optional[Session] = None,
    session_id: Optional[uuid.UUID] = None,
    user_id: Optional[str] = None,
    current_ui_state: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[str, None]:
    """Generate SSE streaming response.

    Yields SSE-formatted events:
    - event: token — individual response tokens (real-time from Claude Sonnet)
    - event: ui_command — UI navigation/filter commands
    - event: headcount_preview — headcount entity extraction preview
    - event: done — final event with full content for persistence

    Deterministic intents (headcount, navigation, filters) use pre-computed
    responses for speed. General / conversational queries are streamed in
    real-time from Claude claude-3-5-sonnet.
    """
    intent, param = parse_intent(message)

    metadata: Dict[str, Any] = {
        "intent": intent,
        "param": param,
        "model": "deployable-chat",
        "agent": "chat-assistant",
    }
    # Attach UI context summary for downstream observability
    ui_ctx_summary = _build_ui_context_summary(current_ui_state)
    if ui_ctx_summary:
        metadata["ui_context"] = ui_ctx_summary

    # ── Deterministic paths: headcount, confirm, navigation, filters ──
    if intent == "headcount_request" and db:
        entities = extract_headcount_entities(message) or {"role": None, "count": 1, "location": None}
        response_text, ui_commands, hc_metadata = generate_headcount_response(db, entities, user_role)
        metadata.update(hc_metadata)

        # Send headcount preview event first
        preview_data = json.dumps({
            "entities": entities,
            "confirmation_path": hc_metadata.get("confirmation_path"),
            "ready_to_confirm": hc_metadata.get("ready_to_confirm", False),
            "matching_projects": hc_metadata.get("matching_projects", []),
            "partner": hc_metadata.get("partner"),
        })
        yield f"event: headcount_preview\ndata: {preview_data}\n\n"

        # Stream the deterministic response
        async for event in _stream_deterministic_response(response_text, ui_commands, metadata):
            yield event
        return

    if intent == "headcount_confirm" and db and session_id and user_id:
        pending_ctx = _get_pending_headcount_context(db, session_id, user_id)
        if pending_ctx and pending_ctx.get("entities"):
            entities = pending_ctx["entities"]
            partner = pending_ctx.get("partner")
            matching_projects = pending_ctx.get("matching_projects", [])

            try:
                headcount = confirm_headcount_request(
                    db=db,
                    user_id=user_id,
                    role_name=entities.get("role", "Technician"),
                    quantity=entities.get("count", 1),
                    location=entities.get("location"),
                    partner_id=partner.get("id") if partner else None,
                    project_id=matching_projects[0]["id"] if matching_projects else None,
                )
                db.commit()

                location_str = f" in {entities.get('location')}" if entities.get('location') else ""
                response_text = (
                    f"Headcount request created successfully!\n\n"
                    f"- **Request ID:** `{str(headcount.id)[:8]}...`\n"
                    f"- **Role:** {headcount.role_name}\n"
                    f"- **Quantity:** {headcount.quantity}\n"
                    f"- **Status:** Pending review{location_str}\n\n"
                    f"The request is now in the **Pending** queue for ops review."
                )
                ui_commands = [
                    {"action": "navigate", "target": "/ops/inbox", "label": "Open Agent Inbox"},
                    {"action": "toast", "target": "success",
                     "params": {"message": f"Headcount request created: {headcount.quantity} {headcount.role_name}"},
                     "label": "Request Created"},
                ]
                metadata = {
                    "intent": "headcount_confirmed",
                    "headcount_request_id": str(headcount.id),
                }
            except ValueError as e:
                response_text = f"Sorry, I couldn't create the headcount request: {str(e)}"
                ui_commands = []
                metadata = {"intent": "headcount_confirm_failed", "error": str(e)}
        else:
            response_text, ui_commands = generate_response(
                "general", None, user_role, current_ui_state=current_ui_state,
            )

        async for event in _stream_deterministic_response(response_text, ui_commands, metadata):
            yield event
        return

    # ── LLM-powered path: general + conversational queries ──
    # For "general" intent or any intent the LLM can enhance, call Claude Sonnet
    if intent == "general":
        async for event in _stream_claude_response(
            message, user_role, session_messages, metadata,
            ui_context_summary=ui_ctx_summary,
        ):
            yield event
        return

    # ── Deterministic intent with known response — fast path ──
    response_text, ui_commands = generate_response(
        intent, param, user_role, current_ui_state=current_ui_state,
    )

    async for event in _stream_deterministic_response(response_text, ui_commands, metadata):
        yield event
