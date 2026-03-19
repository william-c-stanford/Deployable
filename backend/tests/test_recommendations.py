"""Tests for recommendation API endpoints and WebSocket broadcast."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.recommendation import (
    Recommendation,
    RecommendationStatus,
    RecommendationType,
)
from app.schemas.recommendation import (
    RecommendationCreate,
    RecommendationResponse,
    RecommendationListResponse,
    RecommendationActionRequest,
    RecommendationActionResponse,
    ScorecardDimension,
    WebSocketRecommendationEvent,
)


# ---------------------------------------------------------------------------
# Schema unit tests (no DB required)
# ---------------------------------------------------------------------------

class TestRecommendationSchemas:
    """Test Pydantic schemas for recommendations."""

    def test_scorecard_dimension_valid(self):
        sc = ScorecardDimension(
            skills_match=0.9,
            availability=0.8,
            location=0.7,
            experience=0.85,
            certifications=0.95,
        )
        assert sc.skills_match == 0.9
        assert sc.certifications == 0.95

    def test_scorecard_dimension_bounds(self):
        with pytest.raises(Exception):
            ScorecardDimension(skills_match=1.5)  # > 1
        with pytest.raises(Exception):
            ScorecardDimension(availability=-0.1)  # < 0

    def test_recommendation_create_defaults(self):
        rc = RecommendationCreate(recommendation_type="staffing")
        assert rc.status == "Pending"
        assert rc.recommendation_type == "staffing"
        assert rc.scorecard is None

    def test_recommendation_create_with_scorecard(self):
        rc = RecommendationCreate(
            recommendation_type="staffing",
            scorecard={
                "skills_match": 0.9,
                "availability": 0.8,
                "location": 0.7,
                "experience": 0.85,
                "certifications": 0.95,
            },
            explanation="Top candidate for fiber splicing role",
            agent_name="staffing-agent",
            technician_id="tech-123",
            project_id="proj-456",
            overall_score=0.87,
            rank="1",
        )
        assert rc.scorecard["skills_match"] == 0.9
        assert rc.overall_score == 0.87

    def test_recommendation_action_valid_actions(self):
        for action in ["approve", "reject", "dismiss"]:
            req = RecommendationActionRequest(action=action)
            assert req.action == action

    def test_recommendation_action_with_reason(self):
        req = RecommendationActionRequest(
            action="reject",
            reason="Candidate lacks required OSHA cert",
        )
        assert req.reason == "Candidate lacks required OSHA cert"

    def test_recommendation_response_from_attributes(self):
        """Test that response schema can parse ORM-like objects."""
        resp = RecommendationResponse(
            id=uuid.uuid4(),
            recommendation_type="staffing",
            status="Pending",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        assert resp.status == "Pending"

    def test_recommendation_list_response(self):
        resp = RecommendationListResponse(items=[], total=0, skip=0, limit=20)
        assert resp.total == 0
        assert resp.items == []

    def test_action_response(self):
        resp = RecommendationActionResponse(
            id=uuid.uuid4(),
            previous_status="Pending",
            new_status="Approved",
            message="Recommendation approved successfully",
        )
        assert resp.new_status == "Approved"


class TestRecommendationModel:
    """Test the Recommendation ORM model structure."""

    def test_model_tablename(self):
        assert Recommendation.__tablename__ == "recommendations"

    def test_status_enum_values(self):
        assert RecommendationStatus.PENDING.value == "Pending"
        assert RecommendationStatus.APPROVED.value == "Approved"
        assert RecommendationStatus.REJECTED.value == "Rejected"
        assert RecommendationStatus.DISMISSED.value == "Dismissed"
        assert RecommendationStatus.SUPERSEDED.value == "Superseded"

    def test_type_enum_values(self):
        assert RecommendationType.STAFFING.value == "staffing"
        assert RecommendationType.TRAINING.value == "training"
        assert RecommendationType.CERT_RENEWAL.value == "cert_renewal"
        assert RecommendationType.BACKFILL.value == "backfill"
        assert RecommendationType.NEXT_STEP.value == "next_step"

    def test_model_columns_exist(self):
        """Verify all expected columns are defined on the model."""
        expected_columns = {
            "id", "recommendation_type", "target_entity_type",
            "target_entity_id", "role_id", "technician_id",
            "project_id", "rank", "overall_score", "scorecard",
            "explanation", "status", "agent_name", "batch_id",
            "rejection_reason", "metadata", "created_at", "updated_at",
        }
        actual_columns = {c.name for c in Recommendation.__table__.columns}
        assert expected_columns.issubset(actual_columns), (
            f"Missing columns: {expected_columns - actual_columns}"
        )


class TestWebSocketEventSchema:
    """Test WebSocket event schemas."""

    def test_recommendation_event(self):
        rec = RecommendationResponse(
            id=uuid.uuid4(),
            recommendation_type="staffing",
            status="Pending",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        event = WebSocketRecommendationEvent(
            event_type="recommendation.created",
            recommendation=rec,
            timestamp=datetime.now(timezone.utc),
        )
        assert event.event_type == "recommendation.created"
        assert event.recommendation.status == "Pending"
