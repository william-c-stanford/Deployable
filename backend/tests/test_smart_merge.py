"""Tests for the smart merge recommendation algorithm.

Validates:
  - In-place update of existing pending recommendations
  - Supersession of disqualified candidates with reasons
  - Addition of new qualifying candidates
  - Terminal status protection (never resurface dismissed/approved/rejected)
  - Score history preservation and context tracking
  - Score delta computation and explanation refresh thresholds
  - Stale recommendation cleanup (candidates no longer in evaluation set)
  - Nightly batch integration via smart_merge_nightly_batch
  - Technician-level refresh via smart_merge_for_technician
  - Preference rule change re-evaluation
"""

import uuid
import pytest
from datetime import datetime, timezone, date
from unittest.mock import MagicMock, patch, call

from app.services.smart_merge import (
    smart_merge_for_role,
    smart_merge_nightly_batch,
    smart_merge_for_technician,
    smart_merge_on_preference_rule_change,
    SmartMergeResult,
    MergeAction,
    _get_terminal_tech_ids,
    _get_assigned_tech_ids,
    _build_existing_pending_map,
    _compute_score_delta,
    _should_refresh_explanation,
    _preserve_prior_context,
    TERMINAL_STATUSES,
    SCORE_DRIFT_THRESHOLD,
)
from app.models.recommendation import Recommendation, RecommendationStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_recommendation(
    tech_id="tech-1",
    role_id="role-1",
    project_id="proj-1",
    status="Pending",
    overall_score=75.0,
    recommendation_type="staffing",
    batch_id="old-batch",
    explanation="Old explanation",
    metadata=None,
    rejection_reason=None,
    created_at=None,
    updated_at=None,
):
    """Create a mock Recommendation object."""
    rec = MagicMock(spec=Recommendation)
    rec.id = uuid.uuid4()
    rec.target_entity_id = tech_id
    rec.technician_id = tech_id
    rec.role_id = role_id
    rec.project_id = project_id
    rec.status = status
    rec.overall_score = overall_score
    rec.recommendation_type = recommendation_type
    rec.batch_id = batch_id
    rec.explanation = explanation
    rec.metadata_ = metadata or {}
    rec.rejection_reason = rejection_reason
    rec.created_at = created_at or datetime.now(timezone.utc)
    rec.updated_at = updated_at or datetime.now(timezone.utc)
    rec.scorecard = {"overall_score": overall_score}
    rec.rank = "1"
    return rec


def make_evaluation(
    tech_id="tech-1",
    role_id="role-1",
    overall_score=80.0,
    disqualified=False,
    disqualification_reason=None,
):
    """Create a scorecard evaluation dict from the scoring engine."""
    return {
        "technician_id": tech_id,
        "role_id": role_id,
        "overall_score": overall_score,
        "disqualified": disqualified,
        "disqualification_reason": disqualification_reason,
        "dimensions": {
            "skills_match": {"score": overall_score, "detail": "test"},
            "certification_fit": {"score": 80.0, "detail": "test"},
            "availability": {"score": 90.0, "detail": "test"},
            "location_fit": {"score": 70.0, "detail": "test"},
            "experience": {"score": 60.0, "detail": "test"},
        },
        "preference_adjustments": [],
    }


def make_mock_session(
    pending_recs=None,
    terminal_recs=None,
    assigned_techs=None,
    role=None,
    project=None,
    technician=None,
):
    """Create a mock SQLAlchemy session with pre-configured query results."""
    session = MagicMock()

    # Build query chain mock
    def mock_query(model):
        q = MagicMock()

        if model == Recommendation:
            # Terminal recs query
            def mock_filter(*args, **kwargs):
                fq = MagicMock()
                # We use a simple approach: return different data based on call patterns
                fq.all.return_value = []
                fq.filter.return_value = fq
                return fq
            q.filter.return_value = q
            q.all.return_value = pending_recs or []
            return q

        if hasattr(model, '__name__') and model.__name__ == 'Assignment':
            q.filter.return_value = q
            q.all.return_value = []
            return q

        return q

    session.query.side_effect = mock_query

    # session.get for ProjectRole, Project, Technician
    def mock_get(model, id_val):
        model_name = model.__name__ if hasattr(model, '__name__') else str(model)
        if model_name == 'ProjectRole':
            return role
        elif model_name == 'Project':
            return project
        elif model_name == 'Technician':
            return technician
        elif model_name == 'Recommendation':
            for r in (pending_recs or []):
                if str(r.id) == str(id_val):
                    return r
            return None
        return None

    session.get.side_effect = mock_get
    session.add = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()

    return session


# ---------------------------------------------------------------------------
# Unit Tests — Pure functions
# ---------------------------------------------------------------------------

class TestComputeScoreDelta:
    def test_from_none(self):
        assert _compute_score_delta(None, 75.0) == 75.0

    def test_positive_delta(self):
        assert _compute_score_delta(70.0, 80.0) == 10.0

    def test_negative_delta(self):
        assert _compute_score_delta(80.0, 70.0) == -10.0

    def test_zero_delta(self):
        assert _compute_score_delta(75.0, 75.0) == 0.0

    def test_rounding(self):
        result = _compute_score_delta(70.3, 75.7)
        assert result == 5.4


class TestShouldRefreshExplanation:
    def test_from_none_always_refreshes(self):
        assert _should_refresh_explanation(None, 50.0) is True

    def test_large_drift_refreshes(self):
        assert _should_refresh_explanation(70.0, 80.0) is True

    def test_small_drift_does_not_refresh(self):
        assert _should_refresh_explanation(70.0, 73.0) is False

    def test_exact_threshold_refreshes(self):
        assert _should_refresh_explanation(70.0, 75.0) is True

    def test_negative_drift_refreshes(self):
        assert _should_refresh_explanation(80.0, 70.0) is True


class TestPreservePriorContext:
    def test_initializes_score_history(self):
        rec = make_recommendation(overall_score=75.0, metadata={})
        meta = _preserve_prior_context(rec, {})
        assert "score_history" in meta
        assert len(meta["score_history"]) == 1
        assert meta["score_history"][0]["score"] == 75.0

    def test_appends_to_score_history(self):
        rec = make_recommendation(
            overall_score=80.0,
            metadata={"score_history": [{"score": 70.0, "timestamp": "2026-01-01"}]}
        )
        meta = _preserve_prior_context(rec, {})
        assert len(meta["score_history"]) == 2
        assert meta["score_history"][-1]["score"] == 80.0

    def test_limits_score_history_to_10(self):
        history = [{"score": float(i), "timestamp": f"2026-01-{i:02d}"} for i in range(12)]
        rec = make_recommendation(overall_score=99.0, metadata={"score_history": history})
        meta = _preserve_prior_context(rec, {})
        assert len(meta["score_history"]) == 10

    def test_increments_merge_count(self):
        rec = make_recommendation(metadata={"merge_count": 3})
        meta = _preserve_prior_context(rec, {})
        assert meta["merge_count"] == 4

    def test_preserves_original_created_at(self):
        created = datetime(2026, 1, 15, tzinfo=timezone.utc)
        rec = make_recommendation(created_at=created, metadata={})
        meta = _preserve_prior_context(rec, {})
        assert "original_created_at" in meta

    def test_does_not_overwrite_original_created_at(self):
        rec = make_recommendation(
            metadata={"original_created_at": "2025-12-01T00:00:00+00:00"}
        )
        meta = _preserve_prior_context(rec, {})
        assert meta["original_created_at"] == "2025-12-01T00:00:00+00:00"

    def test_preserves_rejection_reason(self):
        rec = make_recommendation(rejection_reason="Too junior")
        meta = _preserve_prior_context(rec, {})
        assert meta["prior_rejection_reason"] == "Too junior"


# ---------------------------------------------------------------------------
# Integration Tests — smart_merge_for_role
# ---------------------------------------------------------------------------

class TestSmartMergeForRole:
    """Tests for the core smart_merge_for_role function."""

    @patch("app.services.smart_merge._get_terminal_tech_ids")
    @patch("app.services.smart_merge._get_assigned_tech_ids")
    @patch("app.services.smart_merge._build_existing_pending_map")
    def test_updates_existing_pending_rec_in_place(
        self, mock_pending_map, mock_assigned, mock_terminal
    ):
        """Existing pending rec for same tech+role should be updated, not duplicated."""
        existing_rec = make_recommendation(tech_id="tech-1", overall_score=70.0)

        mock_terminal.return_value = set()
        mock_assigned.return_value = set()
        mock_pending_map.return_value = {"tech-1": existing_rec}

        role = MagicMock()
        role.role_name = "Fiber Splicer"
        project = MagicMock()
        project.id = "proj-1"
        project.name = "Metro Fiber"

        session = MagicMock()
        session.get.side_effect = lambda model, id_val: (
            role if hasattr(model, '__name__') and model.__name__ == 'ProjectRole'
            else project if hasattr(model, '__name__') and model.__name__ == 'Project'
            else None
        )

        evaluations = [make_evaluation(tech_id="tech-1", overall_score=85.0)]

        result = smart_merge_for_role(
            session=session,
            role_id="role-1",
            new_evaluations=evaluations,
            batch_id="test-batch",
        )

        assert result.updated == 1
        assert result.added == 0
        assert result.superseded == 0
        assert existing_rec.overall_score == 85.0
        assert existing_rec.batch_id == "test-batch"
        # Should NOT call session.add for existing recs
        assert not session.add.called

    @patch("app.services.smart_merge._get_terminal_tech_ids")
    @patch("app.services.smart_merge._get_assigned_tech_ids")
    @patch("app.services.smart_merge._build_existing_pending_map")
    def test_adds_new_qualifying_candidate(
        self, mock_pending_map, mock_assigned, mock_terminal
    ):
        """New qualifying candidate should be added as a fresh recommendation."""
        mock_terminal.return_value = set()
        mock_assigned.return_value = set()
        mock_pending_map.return_value = {}  # No existing recs

        role = MagicMock()
        role.role_name = "Lead Tech"
        project = MagicMock()
        project.id = "proj-1"
        project.name = "Data Center Build"

        tech = MagicMock()
        tech.full_name = "Jane Smith"

        session = MagicMock()
        session.get.side_effect = lambda model, id_val: (
            role if hasattr(model, '__name__') and model.__name__ == 'ProjectRole'
            else project if hasattr(model, '__name__') and model.__name__ == 'Project'
            else tech if hasattr(model, '__name__') and model.__name__ == 'Technician'
            else None
        )

        evaluations = [make_evaluation(tech_id="tech-new", overall_score=80.0)]

        result = smart_merge_for_role(
            session=session,
            role_id="role-1",
            new_evaluations=evaluations,
        )

        assert result.added == 1
        assert result.updated == 0
        assert session.add.called

    @patch("app.services.smart_merge._get_terminal_tech_ids")
    @patch("app.services.smart_merge._get_assigned_tech_ids")
    @patch("app.services.smart_merge._build_existing_pending_map")
    def test_never_resurfaces_dismissed_tech(
        self, mock_pending_map, mock_assigned, mock_terminal
    ):
        """Technicians with dismissed recommendations should be skipped."""
        mock_terminal.return_value = {"tech-dismissed"}
        mock_assigned.return_value = set()
        mock_pending_map.return_value = {}

        role = MagicMock()
        role.role_name = "Splicer"
        project = MagicMock()
        project.id = "proj-1"
        project.name = "Test"

        session = MagicMock()
        session.get.side_effect = lambda model, id_val: (
            role if hasattr(model, '__name__') and model.__name__ == 'ProjectRole'
            else project if hasattr(model, '__name__') and model.__name__ == 'Project'
            else None
        )

        evaluations = [make_evaluation(tech_id="tech-dismissed", overall_score=95.0)]

        result = smart_merge_for_role(
            session=session,
            role_id="role-1",
            new_evaluations=evaluations,
        )

        assert result.skipped_terminal == 1
        assert result.added == 0
        assert not session.add.called

    @patch("app.services.smart_merge._get_terminal_tech_ids")
    @patch("app.services.smart_merge._get_assigned_tech_ids")
    @patch("app.services.smart_merge._build_existing_pending_map")
    def test_never_resurfaces_approved_tech(
        self, mock_pending_map, mock_assigned, mock_terminal
    ):
        """Technicians with approved recommendations should be skipped."""
        mock_terminal.return_value = {"tech-approved"}
        mock_assigned.return_value = set()
        mock_pending_map.return_value = {}

        role = MagicMock()
        role.role_name = "Splicer"
        project = MagicMock()
        project.id = "proj-1"
        project.name = "Test"

        session = MagicMock()
        session.get.side_effect = lambda model, id_val: (
            role if hasattr(model, '__name__') and model.__name__ == 'ProjectRole'
            else project if hasattr(model, '__name__') and model.__name__ == 'Project'
            else None
        )

        evaluations = [make_evaluation(tech_id="tech-approved", overall_score=90.0)]

        result = smart_merge_for_role(
            session=session,
            role_id="role-1",
            new_evaluations=evaluations,
        )

        assert result.skipped_terminal == 1
        assert result.added == 0

    @patch("app.services.smart_merge._get_terminal_tech_ids")
    @patch("app.services.smart_merge._get_assigned_tech_ids")
    @patch("app.services.smart_merge._build_existing_pending_map")
    def test_supersedes_disqualified_existing_rec(
        self, mock_pending_map, mock_assigned, mock_terminal
    ):
        """Existing pending rec should be superseded when tech becomes disqualified."""
        existing_rec = make_recommendation(tech_id="tech-1", overall_score=70.0)

        mock_terminal.return_value = set()
        mock_assigned.return_value = set()
        mock_pending_map.return_value = {"tech-1": existing_rec}

        role = MagicMock()
        role.role_name = "Splicer"
        project = MagicMock()
        project.id = "proj-1"
        project.name = "Test"

        session = MagicMock()
        session.get.side_effect = lambda model, id_val: (
            role if hasattr(model, '__name__') and model.__name__ == 'ProjectRole'
            else project if hasattr(model, '__name__') and model.__name__ == 'Project'
            else None
        )

        evaluations = [make_evaluation(
            tech_id="tech-1",
            overall_score=30.0,
            disqualified=True,
            disqualification_reason="Missing all required certifications",
        )]

        result = smart_merge_for_role(
            session=session,
            role_id="role-1",
            new_evaluations=evaluations,
        )

        assert result.superseded == 1
        assert existing_rec.status == RecommendationStatus.SUPERSEDED.value
        assert "disqualified" in existing_rec.explanation.lower()

    @patch("app.services.smart_merge._get_terminal_tech_ids")
    @patch("app.services.smart_merge._get_assigned_tech_ids")
    @patch("app.services.smart_merge._build_existing_pending_map")
    def test_supersedes_stale_recs_not_in_new_evaluations(
        self, mock_pending_map, mock_assigned, mock_terminal
    ):
        """Pending recs for techs not in the new evaluation set should be superseded."""
        stale_rec = make_recommendation(tech_id="tech-stale", overall_score=65.0)

        mock_terminal.return_value = set()
        mock_assigned.return_value = set()
        mock_pending_map.return_value = {"tech-stale": stale_rec}

        role = MagicMock()
        role.role_name = "Splicer"
        project = MagicMock()
        project.id = "proj-1"
        project.name = "Test"

        tech = MagicMock()
        tech.full_name = "New Tech"

        session = MagicMock()
        session.get.side_effect = lambda model, id_val: (
            role if hasattr(model, '__name__') and model.__name__ == 'ProjectRole'
            else project if hasattr(model, '__name__') and model.__name__ == 'Project'
            else tech if hasattr(model, '__name__') and model.__name__ == 'Technician'
            else None
        )

        # New eval has a different tech, not tech-stale
        evaluations = [make_evaluation(tech_id="tech-new", overall_score=90.0)]

        result = smart_merge_for_role(
            session=session,
            role_id="role-1",
            new_evaluations=evaluations,
        )

        assert result.superseded == 1
        assert result.added == 1
        assert stale_rec.status == RecommendationStatus.SUPERSEDED.value
        assert "no longer in top candidates" in stale_rec.explanation.lower()

    @patch("app.services.smart_merge._get_terminal_tech_ids")
    @patch("app.services.smart_merge._get_assigned_tech_ids")
    @patch("app.services.smart_merge._build_existing_pending_map")
    def test_unchanged_score_tracked(
        self, mock_pending_map, mock_assigned, mock_terminal
    ):
        """Recs with negligible score change should be counted as unchanged."""
        existing_rec = make_recommendation(tech_id="tech-1", overall_score=75.0)

        mock_terminal.return_value = set()
        mock_assigned.return_value = set()
        mock_pending_map.return_value = {"tech-1": existing_rec}

        role = MagicMock()
        role.role_name = "Splicer"
        project = MagicMock()
        project.id = "proj-1"
        project.name = "Test"

        session = MagicMock()
        session.get.side_effect = lambda model, id_val: (
            role if hasattr(model, '__name__') and model.__name__ == 'ProjectRole'
            else project if hasattr(model, '__name__') and model.__name__ == 'Project'
            else None
        )

        evaluations = [make_evaluation(tech_id="tech-1", overall_score=75.0)]

        result = smart_merge_for_role(
            session=session,
            role_id="role-1",
            new_evaluations=evaluations,
        )

        assert result.unchanged == 1
        assert result.updated == 0

    @patch("app.services.smart_merge._get_terminal_tech_ids")
    @patch("app.services.smart_merge._get_assigned_tech_ids")
    @patch("app.services.smart_merge._build_existing_pending_map")
    def test_explanation_refreshed_on_large_score_drift(
        self, mock_pending_map, mock_assigned, mock_terminal
    ):
        """Explanation should be regenerated when score changes by >= threshold."""
        existing_rec = make_recommendation(tech_id="tech-1", overall_score=60.0)

        mock_terminal.return_value = set()
        mock_assigned.return_value = set()
        mock_pending_map.return_value = {"tech-1": existing_rec}

        role = MagicMock()
        role.role_name = "Splicer"
        project = MagicMock()
        project.id = "proj-1"
        project.name = "Test"

        tech = MagicMock()
        tech.full_name = "John Doe"

        session = MagicMock()
        session.get.side_effect = lambda model, id_val: (
            role if hasattr(model, '__name__') and model.__name__ == 'ProjectRole'
            else project if hasattr(model, '__name__') and model.__name__ == 'Project'
            else tech if hasattr(model, '__name__') and model.__name__ == 'Technician'
            else None
        )

        mock_explain = MagicMock(return_value="Updated explanation after score drift")

        evaluations = [make_evaluation(tech_id="tech-1", overall_score=80.0)]  # +20 pts

        result = smart_merge_for_role(
            session=session,
            role_id="role-1",
            new_evaluations=evaluations,
            generate_explanation_fn=mock_explain,
        )

        assert result.updated == 1
        mock_explain.assert_called_once()
        assert existing_rec.explanation == "Updated explanation after score drift"

    @patch("app.services.smart_merge._get_terminal_tech_ids")
    @patch("app.services.smart_merge._get_assigned_tech_ids")
    @patch("app.services.smart_merge._build_existing_pending_map")
    def test_skips_currently_assigned_techs(
        self, mock_pending_map, mock_assigned, mock_terminal
    ):
        """Techs currently assigned to the role should be skipped."""
        mock_terminal.return_value = set()
        mock_assigned.return_value = {"tech-assigned"}
        mock_pending_map.return_value = {}

        role = MagicMock()
        role.role_name = "Splicer"
        project = MagicMock()
        project.id = "proj-1"
        project.name = "Test"

        session = MagicMock()
        session.get.side_effect = lambda model, id_val: (
            role if hasattr(model, '__name__') and model.__name__ == 'ProjectRole'
            else project if hasattr(model, '__name__') and model.__name__ == 'Project'
            else None
        )

        evaluations = [make_evaluation(tech_id="tech-assigned", overall_score=95.0)]

        result = smart_merge_for_role(
            session=session,
            role_id="role-1",
            new_evaluations=evaluations,
        )

        assert result.added == 0
        assert not session.add.called

    @patch("app.services.smart_merge._get_terminal_tech_ids")
    @patch("app.services.smart_merge._get_assigned_tech_ids")
    @patch("app.services.smart_merge._build_existing_pending_map")
    def test_respects_max_recommendations(
        self, mock_pending_map, mock_assigned, mock_terminal
    ):
        """Should not add beyond max_recommendations limit."""
        mock_terminal.return_value = set()
        mock_assigned.return_value = set()
        mock_pending_map.return_value = {}

        role = MagicMock()
        role.role_name = "Splicer"
        project = MagicMock()
        project.id = "proj-1"
        project.name = "Test"

        tech = MagicMock()
        tech.full_name = "Test Tech"

        session = MagicMock()
        session.get.side_effect = lambda model, id_val: (
            role if hasattr(model, '__name__') and model.__name__ == 'ProjectRole'
            else project if hasattr(model, '__name__') and model.__name__ == 'Project'
            else tech if hasattr(model, '__name__') and model.__name__ == 'Technician'
            else None
        )

        # Create 15 evaluations but set max to 3
        evaluations = [
            make_evaluation(tech_id=f"tech-{i}", overall_score=90.0 - i)
            for i in range(15)
        ]

        result = smart_merge_for_role(
            session=session,
            role_id="role-1",
            new_evaluations=evaluations,
            max_recommendations=3,
        )

        assert result.added == 3

    @patch("app.services.smart_merge._get_terminal_tech_ids")
    @patch("app.services.smart_merge._get_assigned_tech_ids")
    @patch("app.services.smart_merge._build_existing_pending_map")
    def test_preserves_score_history_on_update(
        self, mock_pending_map, mock_assigned, mock_terminal
    ):
        """Score history should be maintained across merges."""
        existing_rec = make_recommendation(
            tech_id="tech-1",
            overall_score=70.0,
            metadata={"score_history": [{"score": 65.0, "timestamp": "2026-01-01"}]},
        )

        mock_terminal.return_value = set()
        mock_assigned.return_value = set()
        mock_pending_map.return_value = {"tech-1": existing_rec}

        role = MagicMock()
        role.role_name = "Splicer"
        project = MagicMock()
        project.id = "proj-1"
        project.name = "Test"

        session = MagicMock()
        session.get.side_effect = lambda model, id_val: (
            role if hasattr(model, '__name__') and model.__name__ == 'ProjectRole'
            else project if hasattr(model, '__name__') and model.__name__ == 'Project'
            else None
        )

        evaluations = [make_evaluation(tech_id="tech-1", overall_score=80.0)]

        smart_merge_for_role(
            session=session,
            role_id="role-1",
            new_evaluations=evaluations,
        )

        meta = existing_rec.metadata_
        assert len(meta["score_history"]) == 2
        assert meta["score_history"][-1]["score"] == 70.0  # Previous score appended
        assert meta["merge_count"] == 1

    @patch("app.services.smart_merge._get_terminal_tech_ids")
    @patch("app.services.smart_merge._get_assigned_tech_ids")
    @patch("app.services.smart_merge._build_existing_pending_map")
    def test_result_to_dict_serialization(
        self, mock_pending_map, mock_assigned, mock_terminal
    ):
        """SmartMergeResult.to_dict() should be JSON-serializable."""
        mock_terminal.return_value = set()
        mock_assigned.return_value = set()
        mock_pending_map.return_value = {}

        role = MagicMock()
        role.role_name = "Splicer"
        project = MagicMock()
        project.id = "proj-1"
        project.name = "Test"

        tech = MagicMock()
        tech.full_name = "Test"

        session = MagicMock()
        session.get.side_effect = lambda model, id_val: (
            role if hasattr(model, '__name__') and model.__name__ == 'ProjectRole'
            else project if hasattr(model, '__name__') and model.__name__ == 'Project'
            else tech if hasattr(model, '__name__') and model.__name__ == 'Technician'
            else None
        )

        evaluations = [make_evaluation(tech_id="tech-1", overall_score=80.0)]
        result = smart_merge_for_role(
            session=session,
            role_id="role-1",
            new_evaluations=evaluations,
        )

        d = result.to_dict()
        assert isinstance(d, dict)
        assert "role_id" in d
        assert "updated" in d
        assert "superseded" in d
        assert "added" in d
        assert "actions" in d
        assert isinstance(d["actions"], list)

    @patch("app.services.smart_merge._get_terminal_tech_ids")
    @patch("app.services.smart_merge._get_assigned_tech_ids")
    @patch("app.services.smart_merge._build_existing_pending_map")
    def test_mixed_scenario(
        self, mock_pending_map, mock_assigned, mock_terminal
    ):
        """End-to-end: mix of updates, adds, supersessions, and skips."""
        existing_kept = make_recommendation(tech_id="tech-kept", overall_score=70.0)
        existing_dropped = make_recommendation(tech_id="tech-dropped", overall_score=60.0)
        existing_disqualified = make_recommendation(tech_id="tech-disq", overall_score=50.0)

        mock_terminal.return_value = {"tech-dismissed"}
        mock_assigned.return_value = {"tech-assigned"}
        mock_pending_map.return_value = {
            "tech-kept": existing_kept,
            "tech-dropped": existing_dropped,
            "tech-disq": existing_disqualified,
        }

        role = MagicMock()
        role.role_name = "Splicer"
        project = MagicMock()
        project.id = "proj-1"
        project.name = "Test"

        tech = MagicMock()
        tech.full_name = "New Tech"

        session = MagicMock()
        session.get.side_effect = lambda model, id_val: (
            role if hasattr(model, '__name__') and model.__name__ == 'ProjectRole'
            else project if hasattr(model, '__name__') and model.__name__ == 'Project'
            else tech if hasattr(model, '__name__') and model.__name__ == 'Technician'
            else None
        )

        evaluations = [
            make_evaluation(tech_id="tech-kept", overall_score=85.0),        # Update: +15
            make_evaluation(tech_id="tech-new", overall_score=90.0),         # Add
            make_evaluation(tech_id="tech-dismissed", overall_score=95.0),   # Skip (terminal)
            make_evaluation(tech_id="tech-assigned", overall_score=88.0),    # Skip (assigned)
            make_evaluation(tech_id="tech-disq", overall_score=20.0,         # Supersede (disqualified)
                          disqualified=True, disqualification_reason="Inactive"),
            # tech-dropped is NOT in evaluations → should be superseded as stale
        ]

        result = smart_merge_for_role(
            session=session,
            role_id="role-1",
            new_evaluations=evaluations,
        )

        assert result.updated == 1           # tech-kept
        assert result.added == 1             # tech-new
        assert result.skipped_terminal == 1  # tech-dismissed
        assert result.superseded == 2        # tech-disq + tech-dropped (stale)
        assert existing_kept.overall_score == 85.0
        assert existing_disqualified.status == RecommendationStatus.SUPERSEDED.value
        assert existing_dropped.status == RecommendationStatus.SUPERSEDED.value


class TestSmartMergeResultDataclass:
    def test_total_processed(self):
        r = SmartMergeResult(
            role_id="r1", updated=3, superseded=2, added=1, unchanged=4
        )
        assert r.total_processed == 10

    def test_to_dict_has_all_keys(self):
        r = SmartMergeResult(
            role_id="r1", project_id="p1", batch_id="b1",
            updated=1, superseded=2, added=3, unchanged=4, skipped_terminal=5,
        )
        d = r.to_dict()
        assert d["role_id"] == "r1"
        assert d["total_processed"] == 10
        assert d["skipped_terminal"] == 5


class TestTerminalStatuses:
    """Verify terminal status set is correctly defined."""

    def test_approved_is_terminal(self):
        assert RecommendationStatus.APPROVED.value in TERMINAL_STATUSES

    def test_rejected_is_terminal(self):
        assert RecommendationStatus.REJECTED.value in TERMINAL_STATUSES

    def test_dismissed_is_terminal(self):
        assert RecommendationStatus.DISMISSED.value in TERMINAL_STATUSES

    def test_pending_is_not_terminal(self):
        assert RecommendationStatus.PENDING.value not in TERMINAL_STATUSES

    def test_superseded_is_not_terminal(self):
        assert RecommendationStatus.SUPERSEDED.value not in TERMINAL_STATUSES
