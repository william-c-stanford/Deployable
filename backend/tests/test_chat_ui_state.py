"""Tests for chat endpoint parsing of current_ui_state and context-aware responses.

Sub-AC 3 of AC 17: Backend chat endpoint parses current_ui_state and passes it
to the LangChain agent as tool context for filter-aware responses.
"""

import uuid
from typing import Dict, Any, Optional

import pytest

from app.services.chat_service import (
    _build_ui_context_summary,
    _is_user_already_on_screen,
    _get_active_filter_value,
    generate_response,
    parse_intent,
)
from app.schemas.chat import ChatSendMessage, UIStateContext


# ---------------------------------------------------------------------------
# Unit tests: UI state helper functions
# ---------------------------------------------------------------------------


class TestBuildUIContextSummary:
    """Test the _build_ui_context_summary helper."""

    def test_none_input_returns_none(self):
        assert _build_ui_context_summary(None) is None

    def test_empty_dict_returns_none(self):
        assert _build_ui_context_summary({}) is None

    def test_route_only(self):
        result = _build_ui_context_summary({"current_route": "/ops/technicians"})
        assert result is not None
        assert "Technician Directory" in result
        assert "/ops/technicians" in result

    def test_route_with_filters(self):
        result = _build_ui_context_summary({
            "current_route": "/ops/technicians",
            "active_filters": {"status": "Ready Now", "region": "Texas"},
        })
        assert result is not None
        assert "Technician Directory" in result
        assert "status=Ready Now" in result
        assert "region=Texas" in result

    def test_route_with_tab(self):
        result = _build_ui_context_summary({
            "current_route": "/ops/inbox",
            "active_tab": "recommendations",
        })
        assert result is not None
        assert "Agent Inbox" in result
        assert "recommendations" in result

    def test_selected_entity(self):
        result = _build_ui_context_summary({
            "current_route": "/ops/technicians",
            "selected_entity_id": "abc-123",
            "selected_entity_type": "technician",
        })
        assert result is not None
        assert "technician" in result
        assert "abc-123" in result

    def test_viewport(self):
        result = _build_ui_context_summary({
            "current_route": "/ops/dashboard",
            "viewport": "mobile",
        })
        assert result is not None
        assert "mobile" in result

    def test_unknown_route_uses_path(self):
        result = _build_ui_context_summary({
            "current_route": "/some/custom/route",
        })
        assert result is not None
        assert "/some/custom/route" in result

    def test_empty_filters_not_included(self):
        result = _build_ui_context_summary({
            "current_route": "/ops/dashboard",
            "active_filters": {},
        })
        assert result is not None
        assert "filter" not in result.lower()


class TestIsUserAlreadyOnScreen:
    """Test the _is_user_already_on_screen helper."""

    def test_none_state(self):
        assert _is_user_already_on_screen(None, "/ops/dashboard") is False

    def test_matching_route(self):
        state = {"current_route": "/ops/technicians"}
        assert _is_user_already_on_screen(state, "/ops/technicians") is True

    def test_non_matching_route(self):
        state = {"current_route": "/ops/dashboard"}
        assert _is_user_already_on_screen(state, "/ops/technicians") is False

    def test_trailing_slash_normalization(self):
        state = {"current_route": "/ops/technicians/"}
        assert _is_user_already_on_screen(state, "/ops/technicians") is True

    def test_empty_state(self):
        assert _is_user_already_on_screen({}, "/ops/dashboard") is False


class TestGetActiveFilterValue:
    """Test the _get_active_filter_value helper."""

    def test_none_state(self):
        assert _get_active_filter_value(None, "status") is None

    def test_filter_present(self):
        state = {"active_filters": {"status": "Ready Now", "region": "Texas"}}
        assert _get_active_filter_value(state, "status") == "Ready Now"
        assert _get_active_filter_value(state, "region") == "Texas"

    def test_filter_absent(self):
        state = {"active_filters": {"status": "Ready Now"}}
        assert _get_active_filter_value(state, "region") is None

    def test_empty_filters(self):
        state = {"active_filters": {}}
        assert _get_active_filter_value(state, "status") is None

    def test_no_filters_key(self):
        state = {"current_route": "/ops/technicians"}
        assert _get_active_filter_value(state, "status") is None


# ---------------------------------------------------------------------------
# Integration tests: context-aware response generation
# ---------------------------------------------------------------------------


class TestContextAwareNavigation:
    """Test that navigation intents produce context-aware responses when user
    is already on the target screen."""

    def test_navigate_dashboard_already_there(self):
        ui_state = {"current_route": "/ops/dashboard"}
        response, commands = generate_response("navigate_dashboard", None, "ops", ui_state)
        assert "already" in response.lower()
        assert len(commands) == 0  # No navigation command emitted

    def test_navigate_dashboard_not_there(self):
        ui_state = {"current_route": "/ops/technicians"}
        response, commands = generate_response("navigate_dashboard", None, "ops", ui_state)
        assert "opening" in response.lower()
        assert len(commands) > 0
        assert commands[0]["target"] == "/ops/dashboard"

    def test_navigate_technicians_already_there_with_filters(self):
        ui_state = {
            "current_route": "/ops/technicians",
            "active_filters": {"status": "Ready Now", "region": "Texas"},
        }
        response, commands = generate_response("navigate_technicians", None, "ops", ui_state)
        assert "already" in response.lower()
        assert "Ready Now" in response or "status" in response
        assert len(commands) == 0

    def test_navigate_technicians_already_there_no_filters(self):
        ui_state = {
            "current_route": "/ops/technicians",
            "active_filters": {},
        }
        response, commands = generate_response("navigate_technicians", None, "ops", ui_state)
        assert "already" in response.lower()
        assert "filter" in response.lower()

    def test_navigate_inbox_already_there_with_tab(self):
        ui_state = {
            "current_route": "/ops/inbox",
            "active_tab": "rules",
        }
        response, commands = generate_response("navigate_inbox", None, "ops", ui_state)
        assert "already" in response.lower()
        assert "rules" in response.lower()

    def test_navigate_inbox_not_there(self):
        ui_state = {"current_route": "/ops/dashboard"}
        response, commands = generate_response("navigate_inbox", None, "ops", ui_state)
        assert "opening" in response.lower()
        assert any(c["target"] == "/ops/inbox" for c in commands)


class TestContextAwareFiltering:
    """Test that filter intents are context-aware: skip navigation if already
    on the right screen, and acknowledge existing filters."""

    def test_filter_ready_now_already_on_tech_dir(self):
        ui_state = {"current_route": "/ops/technicians", "active_filters": {}}
        response, commands = generate_response("filter_ready_now", None, "ops", ui_state)
        # Should only emit filter command, NOT navigate command
        assert len(commands) >= 1
        nav_cmds = [c for c in commands if c.get("action") == "navigate"]
        assert len(nav_cmds) == 0  # No navigation needed

    def test_filter_ready_now_already_has_same_filter(self):
        ui_state = {
            "current_route": "/ops/technicians",
            "active_filters": {"status": "Ready Now"},
        }
        response, commands = generate_response("filter_ready_now", None, "ops", ui_state)
        assert "already" in response.lower()
        assert len(commands) == 0  # No action needed

    def test_filter_ready_now_from_different_screen(self):
        ui_state = {"current_route": "/ops/dashboard"}
        response, commands = generate_response("filter_ready_now", None, "ops", ui_state)
        # Should emit both navigate and filter
        nav_cmds = [c for c in commands if c.get("action") == "navigate"]
        filter_cmds = [c for c in commands if c.get("action") == "filter"]
        assert len(nav_cmds) >= 1
        assert len(filter_cmds) >= 1

    def test_filter_by_region_already_on_tech_same_region(self):
        ui_state = {
            "current_route": "/ops/technicians",
            "active_filters": {"region": "Texas"},
        }
        response, commands = generate_response("filter_by_region", "texas", "ops", ui_state)
        assert "already" in response.lower()
        assert len(commands) == 0

    def test_filter_by_region_already_on_tech_different_region(self):
        ui_state = {
            "current_route": "/ops/technicians",
            "active_filters": {"region": "Florida"},
        }
        response, commands = generate_response("filter_by_region", "texas", "ops", ui_state)
        assert "updating" in response.lower() or "Texas" in response
        filter_cmds = [c for c in commands if c.get("action") == "filter"]
        assert len(filter_cmds) >= 1
        nav_cmds = [c for c in commands if c.get("action") == "navigate"]
        assert len(nav_cmds) == 0

    def test_filter_by_skill_already_on_tech(self):
        ui_state = {"current_route": "/ops/technicians"}
        response, commands = generate_response("filter_by_skill", "otdr", "ops", ui_state)
        # Should only filter, not navigate
        nav_cmds = [c for c in commands if c.get("action") == "navigate"]
        assert len(nav_cmds) == 0
        filter_cmds = [c for c in commands if c.get("action") == "filter"]
        assert len(filter_cmds) >= 1

    def test_search_term_already_on_tech(self):
        ui_state = {"current_route": "/ops/technicians"}
        response, commands = generate_response("search_term", "Smith", "ops", ui_state)
        assert "current view" in response.lower()
        nav_cmds = [c for c in commands if c.get("action") == "navigate"]
        assert len(nav_cmds) == 0


class TestContextAwareWithNoUIState:
    """Test that when no UI state is provided, behavior is unchanged (backward compat)."""

    def test_navigate_without_ui_state(self):
        response, commands = generate_response("navigate_dashboard", None, "ops")
        assert "opening" in response.lower()
        assert len(commands) > 0

    def test_filter_without_ui_state(self):
        response, commands = generate_response("filter_ready_now", None, "ops")
        assert len(commands) >= 2  # navigate + filter

    def test_filter_without_ui_state_explicit_none(self):
        response, commands = generate_response("filter_ready_now", None, "ops", None)
        assert len(commands) >= 2


# ---------------------------------------------------------------------------
# Schema tests: ChatSendMessage accepts current_ui_state
# ---------------------------------------------------------------------------


class TestChatSendMessageSchema:
    """Test that the Pydantic schema correctly handles current_ui_state."""

    def test_without_ui_state(self):
        msg = ChatSendMessage(content="hello")
        assert msg.current_ui_state is None

    def test_with_ui_state(self):
        msg = ChatSendMessage(
            content="show me ready now techs",
            current_ui_state=UIStateContext(
                current_route="/ops/technicians",
                active_filters={"status": "Ready Now"},
                active_tab=None,
                viewport="desktop",
            ),
        )
        assert msg.current_ui_state is not None
        assert msg.current_ui_state.current_route == "/ops/technicians"
        assert msg.current_ui_state.active_filters == {"status": "Ready Now"}
        assert msg.current_ui_state.viewport == "desktop"

    def test_ui_state_serialization(self):
        msg = ChatSendMessage(
            content="test",
            current_ui_state=UIStateContext(
                current_route="/ops/inbox",
                active_tab="rules",
                selected_entity_id="abc-123",
                selected_entity_type="technician",
            ),
        )
        data = msg.model_dump()
        assert data["current_ui_state"]["current_route"] == "/ops/inbox"
        assert data["current_ui_state"]["active_tab"] == "rules"
        assert data["current_ui_state"]["selected_entity_id"] == "abc-123"

    def test_ui_state_from_json(self):
        """Test that the schema can be constructed from a JSON-like dict
        (as would come from the frontend)."""
        raw = {
            "content": "find fiber splicers",
            "session_id": None,
            "current_ui_state": {
                "current_route": "/ops/technicians",
                "active_filters": {"region": "Texas"},
                "active_tab": None,
                "selected_entity_id": None,
                "selected_entity_type": None,
                "viewport": "mobile",
            },
        }
        msg = ChatSendMessage(**raw)
        assert msg.current_ui_state is not None
        assert msg.current_ui_state.viewport == "mobile"
        assert msg.current_ui_state.active_filters == {"region": "Texas"}

    def test_partial_ui_state(self):
        """Test that only providing some fields works (all optional)."""
        msg = ChatSendMessage(
            content="test",
            current_ui_state=UIStateContext(current_route="/ops/dashboard"),
        )
        assert msg.current_ui_state.active_filters == {}
        assert msg.current_ui_state.viewport is None
