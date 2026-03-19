"""Tests for the dashboard API endpoint and KPI card logic."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.app.schemas.dashboard import KPICard, DashboardResponse


class TestKPICardSchema:
    """Test KPI card Pydantic schema validation."""

    def test_basic_kpi_card(self):
        card = KPICard(
            id="test-card",
            label="Test Metric",
            value=42,
            icon="Users",
            color="blue",
            link="/test",
        )
        assert card.id == "test-card"
        assert card.value == 42
        assert card.link == "/test"

    def test_kpi_card_with_sub_items(self):
        card = KPICard(
            id="test-card",
            label="Total Technicians",
            value=54,
            icon="Users",
            color="blue",
            link="/technicians",
            sub_items=[
                {"label": "Ready Now", "value": 18, "color": "emerald"},
                {"label": "In Training", "value": 12, "color": "amber"},
            ],
        )
        assert len(card.sub_items) == 2
        assert card.sub_items[0]["label"] == "Ready Now"

    def test_kpi_card_string_value(self):
        card = KPICard(
            id="test",
            label="Status",
            value="Active",
            icon="Check",
            color="green",
            link="/status",
        )
        assert card.value == "Active"

    def test_kpi_card_with_change(self):
        card = KPICard(
            id="test",
            label="Weekly Hours",
            value=1240,
            change=5.2,
            change_label="+5.2% from last week",
            icon="Clock",
            color="cyan",
            link="/hours",
        )
        assert card.change == 5.2
        assert card.change_label is not None


class TestDashboardResponse:
    """Test the full dashboard response schema."""

    def test_empty_dashboard(self):
        resp = DashboardResponse(
            kpi_cards=[],
            suggested_actions=[],
            recent_activity=[],
        )
        assert len(resp.kpi_cards) == 0

    def test_full_dashboard(self):
        resp = DashboardResponse(
            kpi_cards=[
                KPICard(
                    id="total",
                    label="Total",
                    value=54,
                    icon="Users",
                    color="blue",
                    link="/technicians",
                )
            ],
            suggested_actions=[
                {
                    "id": "sa-1",
                    "action_type": "staffing",
                    "title": "Review recommendations",
                    "priority": 5,
                }
            ],
            recent_activity=[
                {
                    "id": "act-1",
                    "action": "Generated recommendations",
                }
            ],
        )
        assert len(resp.kpi_cards) == 1
        assert resp.kpi_cards[0].id == "total"


class TestKPICardNavigation:
    """Test that KPI cards have valid navigation links."""

    def test_all_cards_have_links(self):
        """Every KPI card must have a navigation link."""
        cards = [
            KPICard(id="a", label="A", value=1, icon="Users", color="blue", link="/technicians"),
            KPICard(id="b", label="B", value=2, icon="Briefcase", color="violet", link="/projects?status=Active"),
            KPICard(id="c", label="C", value=3, icon="Inbox", color="rose", link="/inbox"),
        ]
        for card in cards:
            assert card.link.startswith("/")
            assert len(card.link) > 1

    def test_link_with_query_params(self):
        card = KPICard(
            id="filtered",
            label="Ready",
            value=18,
            icon="UserCheck",
            color="emerald",
            link="/technicians?status=Ready+Now",
        )
        assert "?" in card.link
        assert "status=" in card.link
