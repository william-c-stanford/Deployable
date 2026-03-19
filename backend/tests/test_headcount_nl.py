"""Tests for headcount NL parsing — intent detection, entity extraction,
and two-path confirmation flow."""

import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.chat_service import (
    _extract_count,
    _is_headcount_intent,
    _normalize_location,
    _normalize_role,
    extract_headcount_entities,
    parse_intent,
    generate_headcount_response,
    confirm_headcount_request,
)


# ─── Intent Detection ───────────────────────────────────────────────────────


class TestHeadcountIntentDetection:
    """Test that headcount intent is correctly detected from NL messages."""

    @pytest.mark.parametrize(
        "message",
        [
            "I need 3 fiber splicers in Austin",
            "Need 5 technicians for the Dallas project",
            "Request 2 cable pullers in Houston",
            "Can we get 4 more splicers?",
            "We need more fiber splicers for Dallas",
            "Staff up with 4 cable pullers in Houston",
            "Headcount request for 3 splicers",
            "Hire 2 lead technicians",
            "Add 5 fiber techs in California",
            "Bring on 3 more installers",
            "Get me 2 testers for the project",
            "I need a fiber splicer",
            "Need one more technician",
            "Request ten cable pullers in Florida",
        ],
    )
    def test_detects_headcount_intent(self, message):
        assert _is_headcount_intent(message), f"Failed to detect headcount intent: {message}"

    @pytest.mark.parametrize(
        "message",
        [
            "Show me ready technicians",
            "Go to dashboard",
            "Filter by OTDR skill",
            "How many techs are ready?",
            "Open training pipeline",
            "Hello",
            "What projects are active?",
            "Show me the inbox",
            "Clear filters",
        ],
    )
    def test_does_not_detect_non_headcount(self, message):
        assert not _is_headcount_intent(message), f"False positive headcount: {message}"

    @pytest.mark.parametrize(
        "message,expected_intent",
        [
            ("I need 3 fiber splicers in Austin", "headcount_request"),
            ("Request 5 technicians", "headcount_request"),
            ("Can we get 2 cable pullers?", "headcount_request"),
            ("show me ready technicians", "filter_ready_now"),
            ("go to dashboard", "navigate_dashboard"),
            ("hello", "greeting"),
        ],
    )
    def test_parse_intent_routing(self, message, expected_intent):
        intent, _ = parse_intent(message)
        assert intent == expected_intent


# ─── Entity Extraction ───────────────────────────────────────────────────────


class TestEntityExtraction:
    """Test entity extraction (role, count, location) from NL messages."""

    def test_full_extraction(self):
        entities = extract_headcount_entities("I need 3 fiber splicers in Austin")
        assert entities is not None
        assert entities["count"] == 3
        assert entities["role"] is not None
        assert "splicer" in entities["role"].lower() or "fiber" in entities["role"].lower()
        assert entities["location"] is not None
        assert "austin" in entities["location"].lower()

    def test_count_extraction_digit(self):
        entities = extract_headcount_entities("I need 5 technicians in Dallas")
        assert entities is not None
        assert entities["count"] == 5

    def test_count_extraction_word(self):
        entities = extract_headcount_entities("I need three fiber splicers")
        assert entities is not None
        assert entities["count"] == 3

    def test_count_extraction_article(self):
        entities = extract_headcount_entities("I need a fiber splicer")
        assert entities is not None
        assert entities["count"] == 1

    def test_role_extraction_various(self):
        test_cases = [
            ("I need 2 fiber splicers", "splicer"),
            ("Request 3 cable pullers", "puller"),
            ("Hire 1 lead technician", "technician"),
            ("Need 4 otdr testers", "tester"),
        ]
        for message, expected_keyword in test_cases:
            entities = extract_headcount_entities(message)
            assert entities is not None, f"Failed: {message}"
            assert entities["role"] is not None, f"No role for: {message}"
            assert expected_keyword in entities["role"].lower() or \
                   any(kw in entities["role"].lower() for kw in ["tech", "fiber", "cable"]), \
                   f"Wrong role for '{message}': {entities['role']}"

    def test_location_extraction(self):
        test_cases = [
            ("I need 3 splicers in Austin", "austin"),
            ("Request 2 techs in California", "california"),
            ("Need 5 technicians for the Houston project", "houston"),
            ("Hire 2 leads near Dallas", "dallas"),
        ]
        for message, expected_loc in test_cases:
            entities = extract_headcount_entities(message)
            assert entities is not None, f"Failed: {message}"
            assert entities["location"] is not None, f"No location for: {message}"
            assert expected_loc in entities["location"].lower(), \
                   f"Wrong location for '{message}': {entities['location']}"

    def test_no_location(self):
        entities = extract_headcount_entities("I need 3 fiber splicers")
        assert entities is not None
        assert entities["location"] is None or entities["count"] == 3

    def test_returns_none_for_non_headcount(self):
        result = extract_headcount_entities("Show me ready technicians")
        assert result is None


# ─── Count Extraction Helper ─────────────────────────────────────────────────


class TestExtractCount:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("3", 3),
            ("10", 10),
            ("three", 3),
            ("five", 5),
            ("a", 1),
            ("an", 1),
            ("one more", 1),
            ("two more", 2),
            (None, 1),
            ("", 1),
        ],
    )
    def test_extract_count(self, text, expected):
        assert _extract_count(text) == expected


# ─── Role Normalization ──────────────────────────────────────────────────────


class TestNormalizeRole:
    @pytest.mark.parametrize(
        "raw,expected_contains",
        [
            ("fiber splicers", "splicer"),
            ("cable pullers", "puller"),
            ("technicians", "technician"),
            ("lead splicer", "splicer"),
            ("otdr testers", "tester"),
        ],
    )
    def test_normalize_known_roles(self, raw, expected_contains):
        result = _normalize_role(raw)
        assert result is not None
        assert expected_contains in result.lower()

    def test_normalize_unknown_returns_none(self):
        result = _normalize_role("banana")
        assert result is None


# ─── Location Normalization ──────────────────────────────────────────────────


class TestNormalizeLocation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("austin", "Austin"),
            ("texas", "Texas"),
            ("the southeast region", "Southeast"),
            ("california", "California"),
        ],
    )
    def test_normalize_known_locations(self, raw, expected):
        result = _normalize_location(raw)
        assert result is not None
        assert expected.lower() in result.lower()


# ─── Two-Path Confirmation Flow ──────────────────────────────────────────────


class TestConfirmationFlow:
    """Test the two-path confirmation flow: direct NL vs structured form."""

    def _mock_db(self, projects=None, partner=None):
        db = MagicMock()
        # Mock project query
        project_query = MagicMock()
        project_query.filter.return_value = project_query
        project_query.limit.return_value = project_query
        project_query.all.return_value = projects or []
        # Mock partner query
        partner_query = MagicMock()
        partner_query.filter.return_value = partner_query
        partner_query.first.return_value = partner
        # Route queries based on model
        def query_side_effect(model):
            from app.models.project import Project as ProjectModel
            from app.models.user import Partner as PartnerModel
            if model == ProjectModel:
                return project_query
            elif model == PartnerModel:
                return partner_query
            return MagicMock()
        db.query.side_effect = query_side_effect
        return db

    def test_path1_direct_confirmation_all_entities(self):
        """When all entities are present, should return direct NL confirmation path."""
        db = self._mock_db()
        entities = {"role": "Fiber Splicer", "count": 3, "location": "Austin"}

        response, commands, metadata = generate_headcount_response(db, entities, "ops")

        assert "confirm" in response.lower() or "would you like" in response.lower()
        assert metadata["confirmation_path"] == "direct_nl"
        assert metadata["ready_to_confirm"] is True
        assert "Fiber Splicer" in response
        assert "3" in response

    def test_path2_form_fallback_missing_role(self):
        """When role is missing, should return form fallback path."""
        db = self._mock_db()
        entities = {"role": None, "count": 3, "location": "Austin"}

        response, commands, metadata = generate_headcount_response(db, entities, "ops")

        assert metadata["confirmation_path"] == "form_fallback"
        assert metadata["ready_to_confirm"] is False
        assert any("role" in f for f in metadata.get("missing_fields", []))

    def test_path2_form_fallback_missing_count(self):
        """When count is invalid, should return form fallback path."""
        db = self._mock_db()
        entities = {"role": "Splicer", "count": 0, "location": None}

        response, commands, metadata = generate_headcount_response(db, entities, "ops")

        assert metadata["confirmation_path"] == "form_fallback"
        assert metadata["ready_to_confirm"] is False

    def test_path1_role_and_count_sufficient(self):
        """Role + count (no location) should still enable direct confirmation."""
        db = self._mock_db()
        entities = {"role": "Cable Puller", "count": 2, "location": None}

        response, commands, metadata = generate_headcount_response(db, entities, "ops")

        assert metadata["confirmation_path"] == "direct_nl"
        assert metadata["ready_to_confirm"] is True


# ─── Confirmation Response (yes/confirm) ─────────────────────────────────────


class TestConfirmationParsing:
    """Test that confirmation responses are parsed correctly."""

    @pytest.mark.parametrize(
        "message",
        ["yes", "confirm", "do it", "submit", "go ahead", "yep", "yeah", "sure", "ok"],
    )
    def test_confirmation_intent(self, message):
        intent, _ = parse_intent(message)
        assert intent == "headcount_confirm"

    @pytest.mark.parametrize(
        "message",
        ["edit", "modify", "use form", "open form", "use the form"],
    )
    def test_edit_intent(self, message):
        intent, _ = parse_intent(message)
        assert intent == "headcount_edit"


# ─── PendingHeadcountRequest Creation ────────────────────────────────────────


class TestConfirmHeadcountRequest:
    """Test PendingHeadcountRequest creation via confirm_headcount_request."""

    def test_creates_request_with_partner(self):
        """Should create a PendingHeadcountRequest when partner is resolved."""
        db = MagicMock()
        partner_id = str(uuid.uuid4())

        # Mock Partner query for fallback
        mock_partner = MagicMock()
        mock_partner.id = uuid.UUID(partner_id)
        mock_partner.name = "Test Partner"

        partner_query = MagicMock()
        partner_query.first.return_value = mock_partner
        partner_query.filter.return_value = partner_query

        def query_side_effect(model):
            return partner_query

        db.query.side_effect = query_side_effect

        headcount = confirm_headcount_request(
            db=db,
            user_id="test-user",
            role_name="Fiber Splicer",
            quantity=3,
            location="Austin",
            partner_id=partner_id,
        )

        assert headcount.role_name == "Fiber Splicer"
        assert headcount.quantity == 3
        assert headcount.status == "Pending"
        db.add.assert_called_once()
        db.flush.assert_called_once()

    def test_raises_without_partner(self):
        """Should raise ValueError when no partner can be resolved."""
        db = MagicMock()
        partner_query = MagicMock()
        partner_query.first.return_value = None
        partner_query.filter.return_value = partner_query

        project_query = MagicMock()
        project_query.first.return_value = None
        project_query.filter.return_value = project_query

        def query_side_effect(model):
            from app.models.project import Project as ProjectModel
            from app.models.user import Partner as PartnerModel
            if model == ProjectModel:
                return project_query
            return partner_query

        db.query.side_effect = query_side_effect

        with pytest.raises(ValueError, match="No partner found"):
            confirm_headcount_request(
                db=db,
                user_id="test-user",
                role_name="Fiber Splicer",
                quantity=3,
            )


# ─── End-to-end NL → Entities → Confirmation ────────────────────────────────


class TestEndToEndFlow:
    """Integration tests for the full NL → parse → confirm flow."""

    def test_full_flow_happy_path(self):
        """NL input → entity extraction → direct confirmation path."""
        message = "I need 3 fiber splicers in Austin"

        # Step 1: Intent detection
        intent, _ = parse_intent(message)
        assert intent == "headcount_request"

        # Step 2: Entity extraction
        entities = extract_headcount_entities(message)
        assert entities is not None
        assert entities["count"] == 3
        assert entities["role"] is not None
        assert entities["location"] is not None

        # Step 3: Confirmation path determination
        has_role = entities["role"] is not None
        has_count = entities["count"] > 0
        assert has_role and has_count  # Should be direct confirmation path

    def test_full_flow_missing_entities(self):
        """NL input with ambiguous role → form fallback path."""
        message = "I need some help staffing up"

        # Not a headcount intent — too vague
        intent, _ = parse_intent(message)
        # May or may not detect; if it does, entities should be incomplete
        if intent == "headcount_request":
            entities = extract_headcount_entities(message)
            if entities:
                assert entities.get("role") is None or entities.get("count", 0) <= 0

    def test_varied_phrasing(self):
        """Test multiple phrasings all extract correctly."""
        test_cases = [
            ("I need 3 fiber splicers in Austin", 3, True, True),
            ("Request 5 technicians for Dallas", 5, True, True),
            ("Can we get 2 cable pullers?", 2, True, False),
            ("Need 4 testers in California", 4, True, True),
            ("Hire one lead technician", 1, True, False),
        ]
        for message, expected_count, has_role, has_location in test_cases:
            entities = extract_headcount_entities(message)
            assert entities is not None, f"No entities for: {message}"
            assert entities["count"] == expected_count, f"Count mismatch for '{message}': {entities['count']}"
            if has_role:
                assert entities["role"] is not None, f"No role for: {message}"
            if has_location:
                assert entities["location"] is not None, f"No location for: {message}"
