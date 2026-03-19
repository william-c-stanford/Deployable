"""Recommendation and PreferenceRule models."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, Boolean, DateTime, JSON, Float, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class RecommendationStatus(str, enum.Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    DISMISSED = "Dismissed"
    SUPERSEDED = "Superseded"


class RecommendationType(str, enum.Enum):
    STAFFING = "staffing"
    TRAINING = "training"
    CERT_RENEWAL = "cert_renewal"
    BACKFILL = "backfill"
    NEXT_STEP = "next_step"


class PreferenceRuleTemplateType(str, enum.Enum):
    """Template types for preference rules.

    Each template type defines a category of scoring modifier with
    specific parameter schemas that the staffing agent understands.
    """
    SKILL_MINIMUM = "skill_minimum"              # Require minimum proficiency for a skill
    CERT_REQUIRED = "cert_required"              # Require specific certification
    CERT_RECENCY = "cert_recency"                # Prefer certs issued within N months
    REGION_PREFERENCE = "region_preference"      # Prefer/require specific region
    REGION_EXCLUSION = "region_exclusion"         # Exclude specific region
    AVAILABILITY_WINDOW = "availability_window"  # Must be available within N days
    EXPERIENCE_MINIMUM = "experience_minimum"    # Minimum years of experience
    PROJECT_HISTORY = "project_history"           # Prefer/require prior project type experience
    TRAVEL_WILLINGNESS = "travel_willingness"    # Require travel willingness flag
    CLIENT_HISTORY = "client_history"             # Prefer/exclude prior client assignments
    SCORE_THRESHOLD = "score_threshold"           # Minimum overall score threshold
    CUSTOM = "custom"                             # Freeform rule with custom parameters


class PreferenceRuleStatus(str, enum.Enum):
    """Lifecycle status for preference rules."""
    PROPOSED = "proposed"    # Agent-proposed, awaiting ops review
    ACTIVE = "active"        # Approved and actively applied to scoring
    DISABLED = "disabled"    # Temporarily disabled by ops
    ARCHIVED = "archived"    # Permanently archived, no longer applicable


class PreferenceRuleCreatedByType(str, enum.Enum):
    """Who created the preference rule."""
    AGENT = "agent"          # Auto-proposed by rejection-learning agent
    OPS = "ops"              # Manually created by ops user


class Recommendation(Base):
    """An agent-generated recommendation awaiting human approval."""

    __tablename__ = "recommendations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recommendation_type = Column(String(60), nullable=False)
    target_entity_type = Column(String(60), nullable=True)  # technician, project, role
    target_entity_id = Column(String(200), nullable=True)
    role_id = Column(String(200), nullable=True)
    technician_id = Column(String(200), nullable=True)
    project_id = Column(String(200), nullable=True)
    rank = Column(String(10), nullable=True)
    overall_score = Column(Float, nullable=True)
    scorecard = Column(JSON, nullable=True)
    explanation = Column(Text, nullable=True)
    status = Column(String(30), default=RecommendationStatus.PENDING.value)
    agent_name = Column(String(100), nullable=True)
    batch_id = Column(String(200), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationship to preference rules spawned from this recommendation's rejection
    spawned_preference_rules = relationship(
        "PreferenceRule",
        back_populates="source_rejection",
        foreign_keys="PreferenceRule.rejection_id",
    )


class PreferenceRule(Base):
    """Ops-defined or agent-proposed scoring modifier for the staffing agent.

    Rules are either manually created by ops users or auto-proposed by the
    rejection-learning agent when a recommendation is rejected. Agent-proposed
    rules start in PROPOSED status and require ops approval to become ACTIVE.

    Each rule has a template_type that defines the parameter schema:
    - skill_minimum: {"skill_name": str, "min_level": str}
    - cert_required: {"cert_name": str}
    - cert_recency: {"cert_name": str, "max_months": int}
    - region_preference: {"region": str}
    - region_exclusion: {"region": str}
    - availability_window: {"max_days": int}
    - experience_minimum: {"min_years": int}
    - project_history: {"project_type": str}
    - travel_willingness: {"required": bool}
    - client_history: {"client_name": str, "prefer_or_exclude": str}
    - score_threshold: {"min_score": float}
    - custom: {<any>}
    """

    __tablename__ = "preference_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Template classification
    template_type = Column(
        String(60),
        nullable=False,
        default=PreferenceRuleTemplateType.CUSTOM.value,
        index=True,
        comment="Template type enum: skill_minimum, cert_required, region_preference, etc.",
    )

    # Human-readable rule type label (kept for backward compat, maps to template_type)
    rule_type = Column(String(100), nullable=False)

    # Human-readable description of what this rule does
    description = Column(Text, nullable=True)

    # Rule-specific parameters (schema depends on template_type)
    parameters = Column(JSON, default=dict, nullable=False)

    # Legacy threshold field for backward compatibility
    threshold = Column(String(200), nullable=True)

    # Scope: where this rule applies
    scope = Column(String(60), default="global")  # global, client, project_type, project

    # Scope target ID: e.g. partner_id for client scope, project_type for project_type scope
    scope_target_id = Column(String(200), nullable=True)

    # Effect on scoring
    effect = Column(String(30), default="demote")  # exclude, demote, boost

    # Score modifier: signed float applied as multiplier or additive modifier
    score_modifier = Column(
        Float,
        nullable=True,
        default=0.0,
        comment="Scoring modifier value: negative for demote, positive for boost",
    )

    # Priority/weight for conflict resolution among rules
    priority = Column(Integer, default=0, comment="Higher priority rules take precedence")

    # Lifecycle status
    status = Column(
        String(30),
        default=PreferenceRuleStatus.ACTIVE.value,
        nullable=False,
        index=True,
        comment="Lifecycle status: proposed, active, disabled, archived",
    )

    # Legacy active field, derived from status for backward compatibility
    active = Column(Boolean, default=True)

    # Link to the rejection that spawned this rule (if agent-proposed)
    rejection_id = Column(
        UUID(as_uuid=True),
        ForeignKey("recommendations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Recommendation ID whose rejection triggered this rule proposal",
    )

    # Legacy column kept for backward compat (same as rejection_id)
    source_recommendation_id = Column(UUID(as_uuid=True), nullable=True)

    # Agent-proposed reason text
    proposed_reason = Column(Text, nullable=True)

    # Who created this rule
    created_by_type = Column(
        String(30),
        default=PreferenceRuleCreatedByType.OPS.value,
        nullable=False,
        comment="Who created: agent or ops",
    )

    # The specific user or agent that created it
    created_by_id = Column(
        String(200),
        nullable=True,
        comment="User ID (for ops) or agent name (for agent-created rules)",
    )

    # Approval tracking for agent-proposed rules
    approved_by_id = Column(
        String(200),
        nullable=True,
        comment="Ops user ID who approved a proposed rule",
    )
    approved_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the rule was approved",
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationship back to source rejection
    source_rejection = relationship(
        "Recommendation",
        back_populates="spawned_preference_rules",
        foreign_keys=[rejection_id],
    )
