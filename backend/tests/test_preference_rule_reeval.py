"""Tests for the preference rule re-evaluation trigger pipeline.

Tests the complete flow:
  1. Preference rule CRUD endpoints dispatch events
  2. Events route to reeval_recommendations_for_rule Celery task
  3. Task calls smart_merge_on_preference_rule_change
  4. Smart merge re-scores all pending recommendations with updated rules
  5. WebSocket broadcast pushes updated recommendations to connected clients

Covers:
  - Event routing for PREFERENCE_RULE_CREATED/UPDATED/DELETED
  - reeval_recommendations_for_rule task logic
  - smart_merge_on_preference_rule_change scoring behavior
  - WebSocket broadcast invocations
  - Scoring engine preference rule application
"""

import pytest
import uuid
from datetime import date, timedelta, datetime, timezone
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event_dict(event_type_val, entity_type, entity_id, data=None, actor_id="ops-1"):
    """Build a minimal event_dict as Celery tasks receive."""
    return {
        "event_type": event_type_val,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "actor_id": actor_id,
        "data": data or {},
        "correlation_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _make_mock_technician(
    tech_id=None, name="Test Tech", career_stage="Deployed",
    deployability_status="Ready Now", skills=None, certifications=None,
):
    tech = MagicMock()
    tech.id = tech_id or str(uuid.uuid4())
    tech.full_name = name
    tech.career_stage = career_stage
    tech.deployability_status = deployability_status
    tech.available_from = date.today()
    tech.approved_regions = ["GA"]
    tech.home_base_city = "Atlanta"
    tech.skills = skills or []
    tech.certifications = certifications or []
    return tech


def _make_mock_skill(skill_name, proficiency, hours=0.0):
    s = MagicMock()
    s.id = str(uuid.uuid4())
    s.skill_name = skill_name
    s.proficiency_level = proficiency
    s.training_hours_accumulated = hours
    s.skill = None
    return s


def _make_mock_cert(cert_name, status="Active"):
    c = MagicMock()
    c.id = str(uuid.uuid4())
    c.cert_name = cert_name
    c.status = status
    return c


def _make_mock_role(role_name="Lead Splicer", required_skills=None, required_certs=None):
    r = MagicMock()
    r.id = str(uuid.uuid4())
    r.role_name = role_name
    r.required_skills = required_skills or []
    r.required_certs = required_certs or []
    r.skill_weights = {}
    r.project_id = str(uuid.uuid4())
    r.quantity = 2
    r.filled = 0
    return r


def _make_mock_project(name="Test Project"):
    p = MagicMock()
    p.id = str(uuid.uuid4())
    p.name = name
    p.start_date = date.today() + timedelta(days=14)
    p.location_region = "GA"
    p.location_city = "Atlanta"
    p.status = "Active"
    return p


def _make_mock_recommendation(
    tech_id, role_id, project_id=None, status="Pending",
    overall_score=75.0, recommendation_type="staffing",
):
    rec = MagicMock()
    rec.id = str(uuid.uuid4())
    rec.target_entity_id = str(tech_id)
    rec.technician_id = str(tech_id)
    rec.role_id = str(role_id)
    rec.project_id = str(project_id) if project_id else None
    rec.status = status
    rec.overall_score = overall_score
    rec.recommendation_type = recommendation_type
    rec.scorecard = {"overall_score": overall_score}
    rec.explanation = "Test explanation"
    rec.rejection_reason = None
    rec.metadata_ = {}
    rec.batch_id = None
    rec.created_at = datetime.now(timezone.utc)
    rec.updated_at = datetime.now(timezone.utc)
    return rec


def _make_mock_preference_rule(
    rule_type="experience_threshold",
    effect="demote",
    scope="global",
    parameters=None,
    active=True,
):
    rule = MagicMock()
    rule.id = str(uuid.uuid4())
    rule.rule_type = rule_type
    rule.effect = effect
    rule.scope = scope
    rule.parameters = parameters or {}
    rule.active = active
    rule.threshold = None
    rule.created_at = datetime.now(timezone.utc)
    return rule


# ---------------------------------------------------------------------------
# Event Routing Tests
# ---------------------------------------------------------------------------

class TestPreferenceRuleEventRouting:
    """Verify that preference rule events route to the correct Celery task."""

    def test_preference_rule_created_routes_to_reeval(self):
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType

        tasks = EVENT_TASK_ROUTING[EventType.PREFERENCE_RULE_CREATED]
        assert "app.workers.tasks.recommendation.reeval_recommendations_for_rule" in tasks

    def test_preference_rule_updated_routes_to_reeval(self):
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType

        tasks = EVENT_TASK_ROUTING[EventType.PREFERENCE_RULE_UPDATED]
        assert "app.workers.tasks.recommendation.reeval_recommendations_for_rule" in tasks

    def test_preference_rule_deleted_routes_to_reeval(self):
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType

        tasks = EVENT_TASK_ROUTING[EventType.PREFERENCE_RULE_DELETED]
        assert "app.workers.tasks.recommendation.reeval_recommendations_for_rule" in tasks

    def test_preference_events_are_in_preference_category(self):
        from app.workers.events import EventType, EventCategory, EVENT_CATEGORY_MAP

        for event in [
            EventType.PREFERENCE_RULE_CREATED,
            EventType.PREFERENCE_RULE_UPDATED,
            EventType.PREFERENCE_RULE_DELETED,
        ]:
            assert EVENT_CATEGORY_MAP[event] == EventCategory.PREFERENCE

    def test_all_three_rule_events_route_to_same_task(self):
        """All preference rule changes should trigger the same re-evaluation task."""
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType

        expected_task = "app.workers.tasks.recommendation.reeval_recommendations_for_rule"
        for event_type in [
            EventType.PREFERENCE_RULE_CREATED,
            EventType.PREFERENCE_RULE_UPDATED,
            EventType.PREFERENCE_RULE_DELETED,
        ]:
            tasks = EVENT_TASK_ROUTING[event_type]
            assert expected_task in tasks, f"{event_type} missing reeval task"
            # Each should have exactly 1 handler
            assert len(tasks) == 1, f"{event_type} should have exactly 1 handler"


# ---------------------------------------------------------------------------
# Reeval Task Tests
# ---------------------------------------------------------------------------

class TestReevalRecommendationsForRule:
    """Test the reeval_recommendations_for_rule Celery task."""

    @patch("app.workers.tasks.recommendation.publish_notification")
    @patch("app.workers.tasks.recommendation.publish_badge_count_update")
    @patch("app.workers.tasks.recommendation.publish_recommendation_list_refresh")
    @patch("app.workers.tasks.recommendation.smart_merge_on_preference_rule_change")
    @patch("app.workers.tasks.recommendation.SessionLocal")
    def test_calls_smart_merge_on_rule_created(
        self, mock_session_cls, mock_smart_merge, mock_list_refresh,
        mock_badge, mock_notification,
    ):
        from app.workers.tasks.recommendation import reeval_recommendations_for_rule

        session = MagicMock()
        mock_session_cls.return_value = session
        mock_smart_merge.return_value = {
            "reevaluated": 5, "updated": 3, "superseded": 1, "unchanged": 1,
        }
        session.query.return_value.filter.return_value.count.return_value = 4

        event_dict = _make_event_dict(
            "preference.rule_created", "preference_rule", str(uuid.uuid4()),
            data={"rule_type": "experience_threshold", "scope": "global", "effect": "demote"},
        )

        result = reeval_recommendations_for_rule.run(event_dict)

        assert result["status"] == "reevaluated"
        assert result["total_reevaluated"] == 5
        assert result["updated"] == 3
        assert result["superseded"] == 1
        mock_smart_merge.assert_called_once()

    @patch("app.workers.tasks.recommendation.publish_notification")
    @patch("app.workers.tasks.recommendation.publish_badge_count_update")
    @patch("app.workers.tasks.recommendation.publish_recommendation_list_refresh")
    @patch("app.workers.tasks.recommendation.smart_merge_on_preference_rule_change")
    @patch("app.workers.tasks.recommendation.SessionLocal")
    def test_broadcasts_ws_events_on_changes(
        self, mock_session_cls, mock_smart_merge, mock_list_refresh,
        mock_badge, mock_notification,
    ):
        from app.workers.tasks.recommendation import reeval_recommendations_for_rule

        session = MagicMock()
        mock_session_cls.return_value = session
        mock_smart_merge.return_value = {
            "reevaluated": 3, "updated": 2, "superseded": 1, "unchanged": 0,
        }
        session.query.return_value.filter.return_value.count.return_value = 2

        event_dict = _make_event_dict(
            "preference.rule_updated", "preference_rule", str(uuid.uuid4()),
        )

        reeval_recommendations_for_rule.run(event_dict)

        # Should broadcast list refresh
        mock_list_refresh.assert_called_once()
        refresh_kwargs = mock_list_refresh.call_args
        assert refresh_kwargs[1]["summary"]["action"] == "smart_merge_rule_reevaluated"

        # Should update badge count
        mock_badge.assert_called_once_with(
            badge_type="pending_recommendations",
            count=2,
            role="ops",
        )

        # Should send notification (because superseded > 0)
        mock_notification.assert_called_once()

    @patch("app.workers.tasks.recommendation.publish_notification")
    @patch("app.workers.tasks.recommendation.publish_badge_count_update")
    @patch("app.workers.tasks.recommendation.publish_recommendation_list_refresh")
    @patch("app.workers.tasks.recommendation.smart_merge_on_preference_rule_change")
    @patch("app.workers.tasks.recommendation.SessionLocal")
    def test_no_broadcast_when_nothing_changed(
        self, mock_session_cls, mock_smart_merge, mock_list_refresh,
        mock_badge, mock_notification,
    ):
        from app.workers.tasks.recommendation import reeval_recommendations_for_rule

        session = MagicMock()
        mock_session_cls.return_value = session
        mock_smart_merge.return_value = {
            "reevaluated": 0, "updated": 0, "superseded": 0, "unchanged": 0,
        }

        event_dict = _make_event_dict(
            "preference.rule_deleted", "preference_rule", str(uuid.uuid4()),
        )

        result = reeval_recommendations_for_rule.run(event_dict)

        assert result["status"] == "reevaluated"
        assert result["total_reevaluated"] == 0
        # Should NOT broadcast when nothing was reevaluated
        mock_list_refresh.assert_not_called()
        mock_badge.assert_not_called()
        mock_notification.assert_not_called()

    @patch("app.workers.tasks.recommendation.publish_notification")
    @patch("app.workers.tasks.recommendation.publish_badge_count_update")
    @patch("app.workers.tasks.recommendation.publish_recommendation_list_refresh")
    @patch("app.workers.tasks.recommendation.smart_merge_on_preference_rule_change")
    @patch("app.workers.tasks.recommendation.SessionLocal")
    def test_no_notification_when_no_superseded(
        self, mock_session_cls, mock_smart_merge, mock_list_refresh,
        mock_badge, mock_notification,
    ):
        """Notification should only fire when recommendations are superseded (removed)."""
        from app.workers.tasks.recommendation import reeval_recommendations_for_rule

        session = MagicMock()
        mock_session_cls.return_value = session
        mock_smart_merge.return_value = {
            "reevaluated": 5, "updated": 5, "superseded": 0, "unchanged": 0,
        }
        session.query.return_value.filter.return_value.count.return_value = 5

        event_dict = _make_event_dict(
            "preference.rule_updated", "preference_rule", str(uuid.uuid4()),
        )

        reeval_recommendations_for_rule.run(event_dict)

        # List refresh and badge should still fire
        mock_list_refresh.assert_called_once()
        mock_badge.assert_called_once()
        # But notification should NOT fire (no superseded)
        mock_notification.assert_not_called()


# ---------------------------------------------------------------------------
# Smart Merge on Preference Rule Change Tests
# ---------------------------------------------------------------------------

class TestSmartMergeOnPreferenceRuleChange:
    """Test the smart_merge_on_preference_rule_change algorithm."""

    def test_updates_score_when_rule_changes_scoring(self):
        """A new preference rule should change scores of pending recommendations."""
        from app.services.smart_merge import smart_merge_on_preference_rule_change

        session = MagicMock()
        tech = _make_mock_technician(career_stage="Screened")
        role = _make_mock_role()
        project = _make_mock_project()

        rec = _make_mock_recommendation(
            tech_id=tech.id, role_id=role.id,
            project_id=project.id, overall_score=80.0,
        )

        # Query for active preference rules
        rule = _make_mock_preference_rule(
            rule_type="experience_threshold",
            effect="demote",
            parameters={"min_career_stage": "Deployed"},
        )
        session.query.return_value.filter.return_value.all.side_effect = [
            [rule],    # preference rules query
            [rec],     # pending recommendations query
        ]
        session.get.side_effect = lambda cls, id: {
            str(tech.id): tech,
            str(role.id): role,
            str(project.id): project,
        }.get(str(id))

        # Mock the scoring engine
        with patch("app.services.smart_merge.score_technician_for_role") as mock_score:
            mock_score.return_value = {
                "overall_score": 40.0,  # Score reduced by rule
                "disqualified": False,
                "disqualification_reason": None,
                "dimensions": {},
                "preference_adjustments": [{"rule_type": "experience_threshold", "effect": "demote"}],
            }

            stats = smart_merge_on_preference_rule_change(session)

        assert stats["reevaluated"] == 1
        assert stats["updated"] == 1
        assert stats["superseded"] == 0

    def test_supersedes_when_rule_disqualifies(self):
        """A rule with exclude effect should supersede matching recommendations."""
        from app.services.smart_merge import smart_merge_on_preference_rule_change

        session = MagicMock()
        tech = _make_mock_technician(career_stage="Screened")
        role = _make_mock_role()
        project = _make_mock_project()

        rec = _make_mock_recommendation(
            tech_id=tech.id, role_id=role.id,
            project_id=project.id, overall_score=80.0,
        )

        rule = _make_mock_preference_rule(
            rule_type="experience_threshold",
            effect="exclude",
            parameters={"min_career_stage": "Deployed"},
        )
        session.query.return_value.filter.return_value.all.side_effect = [
            [rule],
            [rec],
        ]
        session.get.side_effect = lambda cls, id: {
            str(tech.id): tech,
            str(role.id): role,
            str(project.id): project,
        }.get(str(id))

        with patch("app.services.smart_merge.score_technician_for_role") as mock_score:
            mock_score.return_value = {
                "overall_score": 0.0,
                "disqualified": True,
                "disqualification_reason": "Excluded by rule: experience_threshold",
                "dimensions": {},
                "preference_adjustments": [],
            }

            stats = smart_merge_on_preference_rule_change(session)

        assert stats["reevaluated"] == 1
        assert stats["superseded"] == 1
        assert stats["updated"] == 0

    def test_unchanged_when_score_difference_negligible(self):
        """Recommendations with < 0.1 score change should be counted as unchanged."""
        from app.services.smart_merge import smart_merge_on_preference_rule_change

        session = MagicMock()
        tech = _make_mock_technician()
        role = _make_mock_role()
        project = _make_mock_project()

        rec = _make_mock_recommendation(
            tech_id=tech.id, role_id=role.id,
            project_id=project.id, overall_score=75.0,
        )

        rule = _make_mock_preference_rule(active=True)
        session.query.return_value.filter.return_value.all.side_effect = [
            [rule],
            [rec],
        ]
        session.get.side_effect = lambda cls, id: {
            str(tech.id): tech,
            str(role.id): role,
            str(project.id): project,
        }.get(str(id))

        with patch("app.services.smart_merge.score_technician_for_role") as mock_score:
            mock_score.return_value = {
                "overall_score": 75.05,  # Negligible change
                "disqualified": False,
                "disqualification_reason": None,
                "dimensions": {},
                "preference_adjustments": [],
            }

            stats = smart_merge_on_preference_rule_change(session)

        assert stats["reevaluated"] == 1
        assert stats["unchanged"] == 1
        assert stats["updated"] == 0

    def test_skips_recs_without_tech_or_role(self):
        """Recommendations without target_entity_id or role_id should be skipped."""
        from app.services.smart_merge import smart_merge_on_preference_rule_change

        session = MagicMock()

        rec_no_tech = _make_mock_recommendation(
            tech_id="", role_id=str(uuid.uuid4()), overall_score=70.0,
        )
        rec_no_tech.target_entity_id = None

        rec_no_role = _make_mock_recommendation(
            tech_id=str(uuid.uuid4()), role_id="", overall_score=70.0,
        )
        rec_no_role.role_id = None

        rule = _make_mock_preference_rule(active=True)
        session.query.return_value.filter.return_value.all.side_effect = [
            [rule],
            [rec_no_tech, rec_no_role],
        ]

        stats = smart_merge_on_preference_rule_change(session)

        assert stats["reevaluated"] == 0
        assert stats["updated"] == 0
        assert stats["superseded"] == 0

    def test_handles_missing_tech_or_role_gracefully(self):
        """If tech or role is deleted, the recommendation should be superseded."""
        from app.services.smart_merge import smart_merge_on_preference_rule_change

        session = MagicMock()

        rec = _make_mock_recommendation(
            tech_id=str(uuid.uuid4()), role_id=str(uuid.uuid4()),
            overall_score=70.0,
        )

        rule = _make_mock_preference_rule(active=True)
        session.query.return_value.filter.return_value.all.side_effect = [
            [rule],
            [rec],
        ]
        # session.get returns None for both tech and role
        session.get.return_value = None

        stats = smart_merge_on_preference_rule_change(session)

        assert stats["superseded"] == 1


# ---------------------------------------------------------------------------
# Scoring Engine Preference Rule Application Tests
# ---------------------------------------------------------------------------

class TestScoringPreferenceRules:
    """Test that the scoring engine correctly applies preference rules."""

    def test_experience_threshold_demotes_junior_tech(self):
        from app.services.scoring import _apply_preference_rule

        rule = _make_mock_preference_rule(
            rule_type="experience_threshold",
            effect="demote",
            parameters={"min_career_stage": "Training Completed"},
        )
        tech = _make_mock_technician(career_stage="Screened")
        dimensions = {"experience": {"score": 25}}

        result = _apply_preference_rule(rule, tech, dimensions)

        assert result is not None
        assert result["effect"] == "demote"
        assert result["multiplier"] == 0.5

    def test_experience_threshold_excludes_junior_tech(self):
        from app.services.scoring import _apply_preference_rule

        rule = _make_mock_preference_rule(
            rule_type="experience_threshold",
            effect="exclude",
            parameters={"min_career_stage": "Deployed"},
        )
        tech = _make_mock_technician(career_stage="In Training")
        dimensions = {}

        result = _apply_preference_rule(rule, tech, dimensions)

        assert result is not None
        assert result["effect"] == "exclude"

    def test_experience_threshold_no_effect_on_senior_tech(self):
        from app.services.scoring import _apply_preference_rule

        rule = _make_mock_preference_rule(
            rule_type="experience_threshold",
            effect="demote",
            parameters={"min_career_stage": "In Training"},
        )
        tech = _make_mock_technician(career_stage="Deployed")
        dimensions = {}

        result = _apply_preference_rule(rule, tech, dimensions)

        assert result is None  # No adjustment needed

    def test_skill_level_minimum_demotes_low_skill(self):
        from app.services.scoring import _apply_preference_rule

        rule = _make_mock_preference_rule(
            rule_type="skill_level_minimum",
            effect="demote",
            parameters={"skill_name": "Fiber Splicing", "min_proficiency": "Advanced"},
        )
        tech = _make_mock_technician(
            skills=[_make_mock_skill("Fiber Splicing", "Intermediate")],
        )
        dimensions = {}

        result = _apply_preference_rule(rule, tech, dimensions)

        assert result is not None
        assert result["effect"] == "demote"
        assert result["multiplier"] == 0.7

    def test_location_restriction_excludes_restricted_region(self):
        from app.services.scoring import _apply_preference_rule

        rule = _make_mock_preference_rule(
            rule_type="location_restriction",
            effect="exclude",
            parameters={"excluded_regions": ["FL", "GA"]},
        )
        tech = _make_mock_technician()
        tech.approved_regions = ["GA", "SC"]
        dimensions = {}

        result = _apply_preference_rule(rule, tech, dimensions)

        assert result is not None
        assert result["effect"] == "exclude"
        assert "GA" in result["reason"]

    def test_unknown_rule_type_returns_none(self):
        from app.services.scoring import _apply_preference_rule

        rule = _make_mock_preference_rule(rule_type="unknown_rule_type")
        tech = _make_mock_technician()
        dimensions = {}

        result = _apply_preference_rule(rule, tech, dimensions)

        assert result is None


# ---------------------------------------------------------------------------
# WebSocket Broadcast Service Tests
# ---------------------------------------------------------------------------

class TestWsBroadcastForRuleChanges:
    """Test that the ws_broadcast functions work correctly for rule changes."""

    @patch("app.services.ws_broadcast._get_redis")
    def test_publish_recommendation_list_refresh(self, mock_get_redis):
        from app.services.ws_broadcast import publish_recommendation_list_refresh

        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        result = publish_recommendation_list_refresh(
            summary={
                "action": "smart_merge_rule_reevaluated",
                "reevaluated": 5,
                "updated": 3,
                "superseded": 1,
                "unchanged": 1,
            },
            pending_count=4,
        )

        assert result is True
        mock_redis.publish.assert_called_once()

        # Verify the published message structure
        import json
        published_args = mock_redis.publish.call_args
        channel = published_args[0][0]
        message = json.loads(published_args[0][1])

        assert channel == "deployable:ws_broadcast"
        assert message["topic"] == "recommendations"
        assert message["event"]["event_type"] == "recommendation.list_refresh"
        assert message["event"]["pending_count"] == 4
        assert message["event"]["summary"]["action"] == "smart_merge_rule_reevaluated"

    @patch("app.services.ws_broadcast._get_redis")
    def test_publish_badge_count_update(self, mock_get_redis):
        from app.services.ws_broadcast import publish_badge_count_update

        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        result = publish_badge_count_update(
            badge_type="pending_recommendations",
            count=4,
            role="ops",
        )

        assert result is True
        mock_redis.publish.assert_called_once()

        import json
        published_args = mock_redis.publish.call_args
        message = json.loads(published_args[0][1])

        assert message["topic"] == "notifications"
        assert message["event"]["event_type"] == "badge_count.updated"
        assert message["event"]["badge_type"] == "pending_recommendations"
        assert message["event"]["count"] == 4

    @patch("app.services.ws_broadcast._get_redis")
    def test_publish_notification_for_rule_reevaluation(self, mock_get_redis):
        from app.services.ws_broadcast import publish_notification

        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        result = publish_notification(
            notification_type="rule_reevaluation",
            title="Preference Rule Applied",
            message="Smart merge re-evaluated 5 recommendations: 3 updated, 1 superseded",
            role="ops",
            severity="info",
            link="/agent-inbox",
        )

        assert result is True
        mock_redis.publish.assert_called_once()

        import json
        published_args = mock_redis.publish.call_args
        message = json.loads(published_args[0][1])

        assert message["event"]["notification_type"] == "rule_reevaluation"
        assert message["event"]["role"] == "ops"
        assert message["event"]["link"] == "/agent-inbox"


# ---------------------------------------------------------------------------
# Integration: End-to-End Event Dispatch Tests
# ---------------------------------------------------------------------------

class TestPreferenceRuleEventDispatch:
    """Test that dispatch_event correctly sends Celery tasks for rule events."""

    @patch("app.workers.celery_app.celery_app")
    def test_dispatch_rule_created_sends_task(self, mock_celery):
        from app.workers.dispatcher import dispatch_event
        from app.workers.events import EventPayload, EventType

        mock_result = MagicMock()
        mock_result.id = "task-123"
        mock_celery.send_task.return_value = mock_result

        payload = EventPayload(
            event_type=EventType.PREFERENCE_RULE_CREATED,
            entity_type="preference_rule",
            entity_id="rule-1",
            actor_id="ops-1",
            data={"rule_type": "experience_threshold"},
        )

        result_ids = dispatch_event(payload)

        assert len(result_ids) == 1
        assert result_ids[0] == "task-123"
        mock_celery.send_task.assert_called_once_with(
            "app.workers.tasks.recommendation.reeval_recommendations_for_rule",
            args=[payload.to_dict()],
            countdown=None,
            queue="preference",
        )

    @patch("app.workers.celery_app.celery_app")
    def test_dispatch_rule_updated_sends_task(self, mock_celery):
        from app.workers.dispatcher import dispatch_event
        from app.workers.events import EventPayload, EventType

        mock_result = MagicMock()
        mock_result.id = "task-456"
        mock_celery.send_task.return_value = mock_result

        payload = EventPayload(
            event_type=EventType.PREFERENCE_RULE_UPDATED,
            entity_type="preference_rule",
            entity_id="rule-2",
            actor_id="ops-1",
            data={"updated_fields": ["effect", "parameters"]},
        )

        result_ids = dispatch_event(payload)

        assert len(result_ids) == 1
        mock_celery.send_task.assert_called_once()
        call_args = mock_celery.send_task.call_args
        assert call_args[0][0] == "app.workers.tasks.recommendation.reeval_recommendations_for_rule"

    @patch("app.workers.celery_app.celery_app")
    def test_dispatch_rule_deleted_sends_task(self, mock_celery):
        from app.workers.dispatcher import dispatch_event
        from app.workers.events import EventPayload, EventType

        mock_result = MagicMock()
        mock_result.id = "task-789"
        mock_celery.send_task.return_value = mock_result

        payload = EventPayload(
            event_type=EventType.PREFERENCE_RULE_DELETED,
            entity_type="preference_rule",
            entity_id="rule-3",
            actor_id="ops-1",
        )

        result_ids = dispatch_event(payload)

        assert len(result_ids) == 1

    @patch("app.workers.celery_app.celery_app")
    def test_dispatch_safe_swallows_errors(self, mock_celery):
        from app.workers.dispatcher import dispatch_event_safe
        from app.workers.events import EventPayload, EventType

        mock_celery.send_task.side_effect = Exception("Redis unavailable")

        payload = EventPayload(
            event_type=EventType.PREFERENCE_RULE_CREATED,
            entity_type="preference_rule",
            entity_id="rule-1",
            actor_id="ops-1",
        )

        # Should NOT raise
        result_ids = dispatch_event_safe(payload)
        assert result_ids == []


# ---------------------------------------------------------------------------
# Smart Merge Context Preservation Tests
# ---------------------------------------------------------------------------

class TestSmartMergeContextPreservation:
    """Test that smart merge preserves prior context during re-evaluation."""

    def test_score_history_is_tracked(self):
        from app.services.smart_merge import _preserve_prior_context

        rec = _make_mock_recommendation(
            tech_id=str(uuid.uuid4()),
            role_id=str(uuid.uuid4()),
            overall_score=75.0,
        )
        rec.metadata_ = {"score_history": [{"score": 70.0, "timestamp": "2024-01-01"}]}

        result = _preserve_prior_context(rec, {"overall_score": 80.0})

        assert "score_history" in result
        assert len(result["score_history"]) == 2
        assert result["score_history"][-1]["score"] == 75.0

    def test_merge_count_increments(self):
        from app.services.smart_merge import _preserve_prior_context

        rec = _make_mock_recommendation(
            tech_id=str(uuid.uuid4()),
            role_id=str(uuid.uuid4()),
        )
        rec.metadata_ = {"merge_count": 3}

        result = _preserve_prior_context(rec, {})

        assert result["merge_count"] == 4

    def test_original_created_at_preserved(self):
        from app.services.smart_merge import _preserve_prior_context

        created = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        rec = _make_mock_recommendation(
            tech_id=str(uuid.uuid4()),
            role_id=str(uuid.uuid4()),
        )
        rec.metadata_ = {}
        rec.created_at = created

        result = _preserve_prior_context(rec, {})

        assert result["original_created_at"] == created.isoformat()

    def test_should_refresh_explanation_on_large_drift(self):
        from app.services.smart_merge import _should_refresh_explanation

        # Large drift should trigger refresh
        assert _should_refresh_explanation(70.0, 80.0) is True
        assert _should_refresh_explanation(80.0, 70.0) is True

        # Small drift should not
        assert _should_refresh_explanation(75.0, 76.0) is False
        assert _should_refresh_explanation(75.0, 74.5) is False

        # None old score always refreshes
        assert _should_refresh_explanation(None, 75.0) is True
