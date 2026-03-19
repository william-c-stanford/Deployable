"""Orchestrator — public entry point for the Deployable agent layer.

Routes incoming requests (EventPayload domain events or free-text chat messages)
to the appropriate sub-agent and returns structured results with correlation
tracking and audit logging.

Architecture:
  orchestrate(input)
    → router (Claude Haiku for classification / deterministic for events)
      → sub-agent dispatch:
          - reranking_chain   → staffing candidate re-ranking
          - forward_staffing  → proactive gap analysis
          - chat              → conversational SSE streaming
          - event_dispatch    → Celery reactive agent pipeline
      → structured OrchestratorResult with correlation_id + audit trail

Model tiers:
  - Claude Haiku:  routing decisions, background scoring
  - Claude Sonnet: conversational chat (handled by chat_service)
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, Field

from agents.config import AgentConfig, load_agent_config

logger = logging.getLogger("deployable.agents.orchestrator")

# ---------------------------------------------------------------------------
# Graceful LangChain import
# ---------------------------------------------------------------------------

_LANGCHAIN_AVAILABLE = False

try:
    from langchain_anthropic import ChatAnthropic
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import JsonOutputParser

    _LANGCHAIN_AVAILABLE = True
except ImportError:
    logger.info("langchain-anthropic not installed; orchestrator uses deterministic routing only")


# ---------------------------------------------------------------------------
# Sub-agent identifiers
# ---------------------------------------------------------------------------

class SubAgent(str, Enum):
    """Available sub-agents the orchestrator can dispatch to."""
    RERANKING = "reranking"
    FORWARD_STAFFING = "forward_staffing"
    CHAT = "chat"
    EVENT_DISPATCH = "event_dispatch"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Structured output schema for the LangChain router chain
# ---------------------------------------------------------------------------

class RoutingDecision(BaseModel):
    """Structured output from the LLM router chain.

    The router chain classifies incoming messages/events and maps them to
    a sub-agent with a confidence score. Low-confidence results trigger
    deterministic fallback.
    """
    sub_agent: str = Field(
        description="Target sub-agent: 'reranking', 'forward_staffing', 'chat', or 'event_dispatch'"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Classification confidence 0.0-1.0"
    )
    reasoning: str = Field(
        description="Brief explanation of why this route was chosen"
    )
    event_type_hint: Optional[str] = Field(
        default=None,
        description="If the input maps to a known event_type (e.g. 'cert.expired'), include it"
    )
    intent_category: Optional[str] = Field(
        default=None,
        description="High-level intent: navigation, filter, action, query, data_entry, or system_event"
    )


# ---------------------------------------------------------------------------
# Router chain prompt (Claude Haiku — cost-efficient routing tier)
# ---------------------------------------------------------------------------

ROUTER_SYSTEM_PROMPT = """\
You are the Deployable routing agent — a fast classifier that determines which \
sub-agent should handle an incoming event or user message.

Deployable is a workforce OS for fiber/data center technicians. Route each input \
to exactly one sub-agent:

1. **reranking** — Requests to rank, score, compare, or evaluate technician candidates \
for a staffing role. Keywords: rank, rerank, score candidates, best candidate, shortlist, \
compare technicians, top picks, evaluate candidates.

2. **forward_staffing** — Requests about proactive gap analysis, upcoming staffing \
shortfalls, 90-day forecasts, or workforce capacity planning. Keywords: gap analysis, \
staffing gap, forward staffing, upcoming gaps, forecast, predict gaps, capacity.

3. **event_dispatch** — Structured domain events from the system (training, certification, \
document, assignment, escalation, technician lifecycle, project, recommendation, preference, \
timesheet, skill breakdown, transitional state, batch jobs). These have an event_type field \
like "cert.expired" or "training.hours_logged".

4. **chat** — General conversational queries, navigation requests ("show me", "go to", \
"open"), data lookups ("how many technicians are ready?"), UI actions, and anything that \
doesn't clearly fit the other categories.

Return a JSON object with these fields:
- sub_agent: one of "reranking", "forward_staffing", "event_dispatch", "chat"
- confidence: 0.0-1.0
- reasoning: brief explanation (1 sentence)
- event_type_hint: if this maps to a known event type string, include it (null otherwise)
- intent_category: one of "navigation", "filter", "action", "query", "data_entry", "system_event" (null if unsure)"""

ROUTER_USER_PROMPT = """\
Classify this input:

{input_text}"""

# Confidence threshold: below this, fall back to deterministic routing
_LLM_CONFIDENCE_THRESHOLD = 0.6

# ---------------------------------------------------------------------------
# LangChain router chain builder
# ---------------------------------------------------------------------------

# Lazy singleton
_router_chain = None
_router_chain_initialized = False


def _build_router_chain(config: Optional[AgentConfig] = None):
    """Build the LangChain router chain using Claude Haiku.

    Returns None if LangChain/Anthropic is not available or no API key is set.
    """
    if not _LANGCHAIN_AVAILABLE:
        return None

    config = config or load_agent_config()
    api_key = config.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.info("No ANTHROPIC_API_KEY; LLM router chain disabled")
        return None

    llm = ChatAnthropic(
        model=config.haiku_model,
        anthropic_api_key=api_key,
        temperature=0.0,  # Deterministic routing
        max_tokens=256,   # Routing decisions are small
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", ROUTER_SYSTEM_PROMPT),
        ("human", ROUTER_USER_PROMPT),
    ])

    parser = JsonOutputParser(pydantic_object=RoutingDecision)

    chain = prompt | llm | parser
    return chain


def _get_router_chain(config: Optional[AgentConfig] = None):
    """Get or lazily initialize the singleton router chain."""
    global _router_chain, _router_chain_initialized
    if not _router_chain_initialized:
        _router_chain = _build_router_chain(config)
        _router_chain_initialized = True
    return _router_chain


def reset_router_chain() -> None:
    """Reset the singleton router chain (useful for testing)."""
    global _router_chain, _router_chain_initialized
    _router_chain = None
    _router_chain_initialized = False


# ---------------------------------------------------------------------------
# Agent Tier & Kind (for the registry)
# ---------------------------------------------------------------------------

class AgentTier(str, Enum):
    """Which Claude model tier the agent should use."""
    HAIKU = "haiku"       # Background/routing — cost-efficient
    SONNET = "sonnet"     # Conversational — higher quality


class AgentKind(str, Enum):
    """Classification of the sub-agent implementation type."""
    LANGCHAIN_CHAIN = "langchain_chain"
    CELERY_TASK = "celery_task"


# ---------------------------------------------------------------------------
# SubAgentDescriptor — metadata for a registered sub-agent
# ---------------------------------------------------------------------------

@dataclass
class SubAgentDescriptor:
    """Metadata descriptor for a registered sub-agent.

    Attributes:
        name: Unique identifier for this sub-agent.
        kind: Whether this is a LangChain chain or Celery task.
        tier: Which Claude model tier (Haiku for background, Sonnet for conversational).
        handled_events: List of event type strings this agent can process.
        description: Human-readable description of what this agent does.
        module_path: Python dotted module path for the agent code.
        entry_point: Primary callable name or Celery task path.
        capabilities: Freeform metadata dict describing agent capabilities.
        is_periodic: Whether this runs on a Celery Beat schedule.
        schedule_description: Human-readable schedule description.
    """

    name: str
    kind: AgentKind
    tier: AgentTier
    handled_events: list[str] = field(default_factory=list)
    description: str = ""
    module_path: str = ""
    entry_point: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    is_periodic: bool = False
    schedule_description: str = ""

    def __repr__(self) -> str:
        return (
            f"SubAgentDescriptor(name={self.name!r}, kind={self.kind.value}, "
            f"tier={self.tier.value}, events={len(self.handled_events)})"
        )


# ---------------------------------------------------------------------------
# SubAgentRegistry — discovers and indexes all sub-agents
# ---------------------------------------------------------------------------

class SubAgentRegistry:
    """Central registry that discovers and indexes all available sub-agents.

    Discovers LangChain chains (reranking_chain, forward_staffing_chain)
    and Celery worker tasks from backend/app/workers/tasks/ with their
    handled event types and capabilities metadata.

    Usage::

        registry = SubAgentRegistry()
        registry.discover_all()

        # Find agents that handle a specific event
        agents = registry.agents_for_event("CERT_EXPIRED")

        # Get a specific agent by name
        agent = registry.get("reranking_chain")

        # List all registered agents
        for agent in registry.all_agents():
            print(agent.name, agent.handled_events)
    """

    def __init__(self) -> None:
        self._agents: dict[str, SubAgentDescriptor] = {}
        self._event_index: dict[str, list[str]] = {}  # event_type -> [agent_name]

    # -- public API ---------------------------------------------------------

    def register(self, agent: SubAgentDescriptor) -> None:
        """Register a sub-agent descriptor and update the event index."""
        if agent.name in self._agents:
            logger.debug("Replacing existing agent registration: %s", agent.name)

        self._agents[agent.name] = agent

        for evt in agent.handled_events:
            self._event_index.setdefault(evt, [])
            if agent.name not in self._event_index[evt]:
                self._event_index[evt].append(agent.name)

        logger.info(
            "Registered sub-agent %s (%s, %s) handling %d event(s)",
            agent.name,
            agent.kind.value,
            agent.tier.value,
            len(agent.handled_events),
        )

    def get(self, name: str) -> Optional[SubAgentDescriptor]:
        """Look up a registered agent by name."""
        return self._agents.get(name)

    def agents_for_event(self, event_type: str) -> list[SubAgentDescriptor]:
        """Return all agents that handle *event_type*, ordered by registration."""
        names = self._event_index.get(event_type, [])
        return [self._agents[n] for n in names if n in self._agents]

    def all_agents(self) -> list[SubAgentDescriptor]:
        """Return every registered sub-agent."""
        return list(self._agents.values())

    def all_event_types(self) -> list[str]:
        """Return a sorted list of every event type that has at least one handler."""
        return sorted(self._event_index.keys())

    @property
    def agent_count(self) -> int:
        """Number of registered agents."""
        return len(self._agents)

    @property
    def event_count(self) -> int:
        """Number of distinct event types with at least one handler."""
        return len(self._event_index)

    # -- bulk discovery -----------------------------------------------------

    def discover_all(self) -> None:
        """Run all discovery routines to populate the registry.

        Discovers:
          1. LangChain chains in agents/ (reranking_chain, forward_staffing_chain)
          2. Celery worker tasks in backend/app/workers/tasks/
        """
        self._register_langchain_chains()
        self._register_worker_tasks()
        logger.info(
            "Discovery complete: %d agents registered, %d event types indexed",
            self.agent_count,
            self.event_count,
        )

    # -- LangChain chain discovery ------------------------------------------

    def _register_langchain_chains(self) -> None:
        """Register the LangChain chains in the agents/ package."""

        # -- reranking_chain ------------------------------------------------
        self.register(SubAgentDescriptor(
            name="reranking_chain",
            kind=AgentKind.LANGCHAIN_CHAIN,
            tier=AgentTier.HAIKU,
            handled_events=[
                "HEADCOUNT_REQUESTED",
                "ROLE_UNFILLED",
                "SCORE_REFRESH_TRIGGERED",
            ],
            description=(
                "LLM re-ranking chain that takes a top-20 SQL pre-filter shortlist "
                "and uses Claude to score candidates across 5 dimensions (skill match, "
                "proximity, availability, cost efficiency, past performance) with "
                "structured scorecards. Falls back to deterministic heuristic scoring "
                "when API key is unavailable."
            ),
            module_path="agents.reranking_chain",
            entry_point="rerank_candidates",
            capabilities={
                "input_type": "RerankingInput",
                "output_type": "RerankingOutput",
                "max_candidates": 25,
                "scoring_dimensions": 5,
                "deterministic_fallback": True,
                "structured_output": True,
            },
        ))

        # -- forward_staffing_chain -----------------------------------------
        self.register(SubAgentDescriptor(
            name="forward_staffing_chain",
            kind=AgentKind.LANGCHAIN_CHAIN,
            tier=AgentTier.HAIKU,
            handled_events=[
                "FORWARD_STAFFING_SCAN_TRIGGERED",
            ],
            description=(
                "LangChain chain for forward staffing gap analysis. Generates "
                "natural-language gap analysis summaries and proactive staffing "
                "recommendations within a 90-day lookahead window."
            ),
            module_path="agents.forward_staffing_chain",
            entry_point="generate_gap_analysis_summary",
            capabilities={
                "gap_analysis": True,
                "gap_recommendation": True,
                "lookahead_days": 90,
                "deterministic_fallback": True,
                "output_format": "natural_language",
            },
        ))

    # -- Celery worker task discovery ---------------------------------------

    def _register_worker_tasks(self) -> None:
        """Register all Celery worker tasks from backend/app/workers/tasks/."""

        # -- training -------------------------------------------------------
        self.register(SubAgentDescriptor(
            name="training",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "TIMESHEET_APPROVED",
                "TRAINING_HOURS_LOGGED",
                "TRAINING_THRESHOLD_MET",
                "TRAINING_COMPLETED",
                "TECHNICIAN_CREATED",
            ],
            description=(
                "Processes training lifecycle: accumulates hours on skills, "
                "checks proficiency advancement thresholds, advances proficiency "
                "levels, updates deployability, and initializes training plans."
            ),
            module_path="backend.app.workers.tasks.training",
            entry_point="process_approved_timesheet",
            capabilities={
                "proficiency_advancement": True,
                "training_plan_init": True,
                "career_stage_update": True,
                "cascading_events": [
                    "TRAINING_HOURS_LOGGED",
                    "TRAINING_THRESHOLD_MET",
                    "PROFICIENCY_ADVANCED",
                    "TRAINING_COMPLETED",
                    "TECHNICIAN_STATUS_CHANGED",
                ],
            },
        ))

        # -- certification --------------------------------------------------
        self.register(SubAgentDescriptor(
            name="certification",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "CERT_ADDED",
                "CERT_RENEWED",
                "CERT_EXPIRED",
                "CERT_REVOKED",
                "CERT_EXPIRING_SOON",
            ],
            description=(
                "Handles certification lifecycle: recalculates deployability, "
                "handles expiry/revocation with renewal recommendations, and "
                "creates proactive renewal alerts."
            ),
            module_path="backend.app.workers.tasks.certification",
            entry_point="recalc_deployability_for_cert",
            capabilities={
                "deployability_recalc": True,
                "renewal_alerts": True,
                "cascading_events": ["TECHNICIAN_STATUS_CHANGED"],
            },
        ))

        # -- document -------------------------------------------------------
        self.register(SubAgentDescriptor(
            name="document",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "DOC_UPLOADED",
                "DOC_VERIFIED",
                "DOC_REJECTED",
                "DOC_EXPIRED",
                "ALL_DOCS_VERIFIED",
            ],
            description=(
                "Manages document lifecycle: checks completeness, updates "
                "deployability when all docs verified, handles rejection "
                "follow-ups, and flags expired documents."
            ),
            module_path="backend.app.workers.tasks.document",
            entry_point="check_doc_completeness",
            capabilities={
                "completeness_check": True,
                "deployability_update": True,
                "cascading_events": [
                    "ALL_DOCS_VERIFIED",
                    "TECHNICIAN_STATUS_CHANGED",
                ],
            },
        ))

        # -- assignment -----------------------------------------------------
        self.register(SubAgentDescriptor(
            name="assignment",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "ASSIGNMENT_CREATED",
                "ASSIGNMENT_STARTED",
                "ASSIGNMENT_ENDED",
                "ASSIGNMENT_CANCELLED",
                "TECHNICIAN_ROLLING_OFF",
            ],
            description=(
                "Manages assignment lifecycle: updates tech deployability, "
                "handles end-of-assignment flows with backfill checks, "
                "reverts availability on cancellation, creates rolling-off alerts."
            ),
            module_path="backend.app.workers.tasks.assignment",
            entry_point="update_tech_status_for_assignment",
            capabilities={
                "status_update": True,
                "backfill_detection": True,
                "rolling_off_alerts": True,
                "cascading_events": ["TECHNICIAN_AVAILABILITY_CHANGED"],
            },
        ))

        # -- recommendation -------------------------------------------------
        self.register(SubAgentDescriptor(
            name="recommendation",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "HEADCOUNT_REQUESTED",
                "ROLE_UNFILLED",
                "PROFICIENCY_ADVANCED",
                "RECOMMENDATION_APPROVED",
                "RECOMMENDATION_REJECTED",
            ],
            description=(
                "Generates ranked staffing recommendations, refreshes via smart "
                "merge, executes human-approved recommendations, and proposes "
                "preference rules from rejection feedback."
            ),
            module_path="backend.app.workers.tasks.recommendation",
            entry_point="generate_staffing_recommendations",
            capabilities={
                "recommendation_generation": True,
                "smart_merge_refresh": True,
                "approval_execution": True,
                "preference_rule_proposal": True,
                "cascading_events": [
                    "ROLE_UNFILLED",
                    "TECHNICIAN_STATUS_CHANGED",
                ],
            },
        ))

        # -- recommendation_tasks (domain-event-triggered recs) -------------
        self.register(SubAgentDescriptor(
            name="recommendation_tasks",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "TRAINING_COMPLETED",
                "PROFICIENCY_ADVANCED",
                "TRAINING_THRESHOLD_MET",
                "TRAINING_HOURS_LOGGED",
                "CERT_ADDED",
                "CERT_RENEWED",
                "CERT_EXPIRED",
                "CERT_EXPIRING_SOON",
                "CERT_REVOKED",
                "DOC_UPLOADED",
                "DOC_VERIFIED",
                "DOC_REJECTED",
                "DOC_EXPIRED",
                "ALL_DOCS_VERIFIED",
                "ASSIGNMENT_CREATED",
                "ASSIGNMENT_STARTED",
                "ASSIGNMENT_ENDED",
                "ASSIGNMENT_CANCELLED",
                "TECHNICIAN_ROLLING_OFF",
            ],
            description=(
                "Domain-event-triggered recommendation handlers. Generates "
                "recommendations on training milestones, cert changes, "
                "document verification, and assignment lifecycle events."
            ),
            module_path="backend.app.workers.tasks.recommendation_tasks",
            entry_point="handle_training_completion",
            capabilities={
                "training_triggered_recs": True,
                "cert_triggered_recs": True,
                "doc_triggered_recs": True,
                "assignment_triggered_recs": True,
            },
        ))

        # -- batch ----------------------------------------------------------
        self.register(SubAgentDescriptor(
            name="batch",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "NIGHTLY_BATCH_TRIGGERED",
                "CERT_EXPIRY_SCAN_TRIGGERED",
                "SCORE_REFRESH_TRIGGERED",
                "NIGHTLY_READINESS_TRIGGERED",
            ],
            description=(
                "Nightly and on-demand batch operations: full score refresh, "
                "cert expiry scan (30-day lookahead), manual score recalculation, "
                "and nightly deployability re-evaluation."
            ),
            module_path="backend.app.workers.tasks.batch",
            entry_point="nightly_batch",
            is_periodic=True,
            schedule_description="nightly_batch 2:00 AM UTC; cert_expiry_scan daily; readiness 2:30 AM UTC",
            capabilities={
                "score_refresh": True,
                "cert_expiry_scan": True,
                "readiness_reeval": True,
                "cascading_events": [
                    "CERT_EXPIRED",
                    "CERT_EXPIRING_SOON",
                    "TECHNICIAN_STATUS_CHANGED",
                ],
            },
        ))

        # -- readiness ------------------------------------------------------
        self.register(SubAgentDescriptor(
            name="readiness",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "CERT_ADDED",
                "CERT_EXPIRED",
                "CERT_REVOKED",
                "CERT_RENEWED",
                "TRAINING_COMPLETED",
                "PROFICIENCY_ADVANCED",
                "ASSIGNMENT_ENDED",
                "ASSIGNMENT_CANCELLED",
                "TECHNICIAN_STATUS_CHANGED",
                "TECHNICIAN_AVAILABILITY_CHANGED",
                "DOC_VERIFIED",
                "ALL_DOCS_VERIFIED",
                "NIGHTLY_BATCH_TRIGGERED",
                "SCORE_REFRESH_TRIGGERED",
            ],
            description=(
                "Re-evaluates technician readiness after domain events. "
                "Aggregates cert, training, assignment, and doc status into "
                "a composite readiness score. Also runs in batch mode nightly."
            ),
            module_path="backend.app.workers.tasks.readiness",
            entry_point="reevaluate_technician_readiness",
            capabilities={
                "readiness_evaluation": True,
                "batch_reeval": True,
            },
        ))

        # -- escalation -----------------------------------------------------
        self.register(SubAgentDescriptor(
            name="escalation",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "ESCALATION_SCAN_TRIGGERED",
                "CONFIRMATION_ESCALATED",
            ],
            description=(
                "Monitors partner confirmations for 24-hour response SLA. "
                "Scans for overdue confirmations and creates escalation items."
            ),
            module_path="backend.app.workers.tasks.escalation",
            entry_point="scan_overdue_confirmations",
            is_periodic=True,
            schedule_description="Every 15 minutes",
            capabilities={
                "sla_monitoring": True,
                "escalation_handling": True,
                "business_rule": "24h partner confirmation window",
            },
        ))

        # -- forward_staffing (Celery task wrapper) -------------------------
        self.register(SubAgentDescriptor(
            name="forward_staffing_task",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "FORWARD_STAFFING_SCAN_TRIGGERED",
            ],
            description=(
                "Celery task wrapper for the forward staffing chain. Runs "
                "90-day gap analysis every 6 hours or on demand."
            ),
            module_path="backend.app.workers.tasks.forward_staffing",
            entry_point="forward_staffing_scan",
            is_periodic=True,
            schedule_description="Every 6 hours",
            capabilities={
                "gap_analysis": True,
                "recommendation_refresh": True,
                "lookahead_days": 90,
            },
        ))

        # -- headcount ------------------------------------------------------
        self.register(SubAgentDescriptor(
            name="headcount",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "HEADCOUNT_APPROVED",
            ],
            description=(
                "Converts approved headcount requests into ProjectRole slots. "
                "Runs after human ops approval -- not autonomous."
            ),
            module_path="backend.app.workers.tasks.headcount",
            entry_point="process_approved_headcount",
            capabilities={
                "headcount_processing": True,
                "requires_human_approval": True,
            },
        ))

        # -- partner_visibility ---------------------------------------------
        self.register(SubAgentDescriptor(
            name="partner_visibility",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "NIGHTLY_BATCH_TRIGGERED",
            ],
            description=(
                "Scans for assignments starting or ending within 48 hours "
                "and creates partner visibility notifications."
            ),
            module_path="backend.app.workers.tasks.partner_visibility",
            entry_point="scan_upcoming_assignments",
            is_periodic=True,
            schedule_description="Every 15 minutes",
            capabilities={
                "assignment_monitoring": True,
                "notification_types": [
                    "ASSIGNMENT_STARTING",
                    "ASSIGNMENT_ENDING",
                ],
            },
        ))

        # -- transitional ---------------------------------------------------
        self.register(SubAgentDescriptor(
            name="transitional",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "TRANSITIONAL_STATE_ENTERED",
            ],
            description=(
                "Manages transitional technician states (Onboarding, Pending Review, "
                "Suspended). Scans for timeout-expired and condition-met transitions."
            ),
            module_path="backend.app.workers.tasks.transitional",
            entry_point="scan_transitional_states",
            is_periodic=True,
            schedule_description="Every 10 minutes",
            capabilities={
                "state_management": True,
                "timeout_detection": True,
                "cascading_events": [
                    "TRANSITIONAL_STATE_RESOLVED",
                    "TECHNICIAN_STATUS_CHANGED",
                ],
            },
        ))

        # -- next_step ------------------------------------------------------
        self.register(SubAgentDescriptor(
            name="next_step",
            kind=AgentKind.CELERY_TASK,
            tier=AgentTier.HAIKU,
            handled_events=[
                "NIGHTLY_BATCH_TRIGGERED",
                "CERT_ADDED",
                "CERT_EXPIRED",
                "CERT_EXPIRING_SOON",
                "TRAINING_COMPLETED",
                "PROFICIENCY_ADVANCED",
                "ASSIGNMENT_ENDED",
                "ASSIGNMENT_CREATED",
                "DOC_VERIFIED",
                "ALL_DOCS_VERIFIED",
                "TECHNICIAN_STATUS_CHANGED",
            ],
            description=(
                "Generates next-step recommendations for technicians and "
                "ops-dashboard action cards. Runs nightly in batch and "
                "reactively on domain events."
            ),
            module_path="backend.app.workers.tasks.next_step",
            entry_point="nightly_next_step_batch",
            is_periodic=True,
            schedule_description="Nightly batch + event-triggered refresh",
            capabilities={
                "next_step_generation": True,
                "ops_action_cards": True,
                "batch_mode": True,
                "event_triggered": True,
            },
        ))


# ---------------------------------------------------------------------------
# Module-level singleton for the registry
# ---------------------------------------------------------------------------

_registry: Optional[SubAgentRegistry] = None


def get_registry() -> SubAgentRegistry:
    """Return the global SubAgentRegistry singleton, discovering on first call."""
    global _registry
    if _registry is None:
        _registry = SubAgentRegistry()
        _registry.discover_all()
    return _registry


def reset_registry() -> None:
    """Reset the global registry (useful for testing)."""
    global _registry
    _registry = None


def _invoke_llm_router(
    input_text: str,
    config: Optional[AgentConfig] = None,
) -> Optional[RoutingDecision]:
    """Invoke the LLM router chain and return a RoutingDecision, or None on failure.

    This is the LLM-based classification path. Returns None if:
    - LangChain is not available
    - No API key
    - LLM call fails
    - Confidence is below threshold
    """
    chain = _get_router_chain(config)
    if chain is None:
        return None

    try:
        result = chain.invoke({"input_text": input_text})
        if isinstance(result, dict):
            decision = RoutingDecision(**result)
        else:
            decision = result

        logger.info(
            "LLM router: sub_agent=%s confidence=%.2f reasoning=%s",
            decision.sub_agent,
            decision.confidence,
            decision.reasoning[:80],
        )
        return decision

    except Exception:
        logger.exception("LLM router chain invocation failed")
        return None


# ---------------------------------------------------------------------------
# Orchestrator result
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorResult:
    """Structured result from an orchestrate() invocation.

    Attributes:
        correlation_id: Unique ID for tracing this request across systems.
        sub_agent: Which sub-agent handled the request.
        status: Outcome status (success, error, routed, fallback).
        data: Sub-agent-specific result payload.
        audit_entries: Audit log entries created during processing.
        processing_time_ms: Wall-clock time for the full orchestration.
        error: Error message if status == "error".
    """
    correlation_id: str
    sub_agent: SubAgent
    status: str = "success"
    data: dict[str, Any] = field(default_factory=dict)
    audit_entries: list[dict[str, Any]] = field(default_factory=list)
    processing_time_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "sub_agent": self.sub_agent.value,
            "status": self.status,
            "data": self.data,
            "audit_entries": self.audit_entries,
            "processing_time_ms": round(self.processing_time_ms, 2),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Router — classifies input to a sub-agent
# ---------------------------------------------------------------------------

# Deterministic routing for EventPayload (no LLM needed)
_EVENT_TYPE_TO_AGENT: dict[str, SubAgent] = {
    # Staffing / recommendation events → event dispatch pipeline
    "project.headcount_requested": SubAgent.EVENT_DISPATCH,
    "project.headcount_approved": SubAgent.EVENT_DISPATCH,
    "project.role_unfilled": SubAgent.EVENT_DISPATCH,
    "recommendation.created": SubAgent.EVENT_DISPATCH,
    "recommendation.approved": SubAgent.EVENT_DISPATCH,
    "recommendation.rejected": SubAgent.EVENT_DISPATCH,
    "recommendation.dismissed": SubAgent.EVENT_DISPATCH,
    # Forward staffing
    "forward_staffing.scan_triggered": SubAgent.FORWARD_STAFFING,
    "forward_staffing.gap_detected": SubAgent.FORWARD_STAFFING,
    # Training / cert / doc / assignment / batch → event dispatch
    "training.hours_logged": SubAgent.EVENT_DISPATCH,
    "training.threshold_met": SubAgent.EVENT_DISPATCH,
    "training.proficiency_advanced": SubAgent.EVENT_DISPATCH,
    "training.completed": SubAgent.EVENT_DISPATCH,
    "cert.added": SubAgent.EVENT_DISPATCH,
    "cert.renewed": SubAgent.EVENT_DISPATCH,
    "cert.expired": SubAgent.EVENT_DISPATCH,
    "cert.expiring_soon": SubAgent.EVENT_DISPATCH,
    "cert.revoked": SubAgent.EVENT_DISPATCH,
    "doc.uploaded": SubAgent.EVENT_DISPATCH,
    "doc.verified": SubAgent.EVENT_DISPATCH,
    "doc.rejected": SubAgent.EVENT_DISPATCH,
    "doc.expired": SubAgent.EVENT_DISPATCH,
    "doc.all_verified": SubAgent.EVENT_DISPATCH,
    "assignment.created": SubAgent.EVENT_DISPATCH,
    "assignment.started": SubAgent.EVENT_DISPATCH,
    "assignment.ended": SubAgent.EVENT_DISPATCH,
    "assignment.cancelled": SubAgent.EVENT_DISPATCH,
    "assignment.rolling_off": SubAgent.EVENT_DISPATCH,
    "confirmation.requested": SubAgent.EVENT_DISPATCH,
    "confirmation.confirmed": SubAgent.EVENT_DISPATCH,
    "confirmation.declined": SubAgent.EVENT_DISPATCH,
    "confirmation.escalated": SubAgent.EVENT_DISPATCH,
    "confirmation.escalation_resolved": SubAgent.EVENT_DISPATCH,
    "batch.escalation_scan": SubAgent.EVENT_DISPATCH,
    "technician.created": SubAgent.EVENT_DISPATCH,
    "technician.status_changed": SubAgent.EVENT_DISPATCH,
    "technician.availability_changed": SubAgent.EVENT_DISPATCH,
    "project.created": SubAgent.EVENT_DISPATCH,
    "project.status_changed": SubAgent.EVENT_DISPATCH,
    "preference.rule_created": SubAgent.EVENT_DISPATCH,
    "preference.rule_updated": SubAgent.EVENT_DISPATCH,
    "preference.rule_deleted": SubAgent.EVENT_DISPATCH,
    "timesheet.submitted": SubAgent.EVENT_DISPATCH,
    "timesheet.approved": SubAgent.EVENT_DISPATCH,
    "timesheet.flagged": SubAgent.EVENT_DISPATCH,
    "timesheet.partner_approved": SubAgent.EVENT_DISPATCH,
    "timesheet.partner_flagged": SubAgent.EVENT_DISPATCH,
    "timesheet.resolved": SubAgent.EVENT_DISPATCH,
    "skill_breakdown.submitted": SubAgent.EVENT_DISPATCH,
    "skill_breakdown.approved": SubAgent.EVENT_DISPATCH,
    "skill_breakdown.rejected": SubAgent.EVENT_DISPATCH,
    "skill_breakdown.revision_requested": SubAgent.EVENT_DISPATCH,
    "transitional.entered": SubAgent.EVENT_DISPATCH,
    "transitional.resolved": SubAgent.EVENT_DISPATCH,
    "transitional.expired": SubAgent.EVENT_DISPATCH,
    "batch.transitional_scan": SubAgent.EVENT_DISPATCH,
    "batch.nightly": SubAgent.EVENT_DISPATCH,
    "batch.nightly_readiness": SubAgent.EVENT_DISPATCH,
    "batch.cert_expiry_scan": SubAgent.EVENT_DISPATCH,
    "batch.score_refresh": SubAgent.EVENT_DISPATCH,
}

# Keywords that hint at re-ranking intent in free-text
_RERANKING_KEYWORDS = [
    "rank", "rerank", "re-rank", "score candidates", "best candidate",
    "top candidates", "shortlist", "evaluate candidates", "rank technicians",
    "score technicians", "compare candidates",
]

# Keywords that hint at forward staffing
_FORWARD_STAFFING_KEYWORDS = [
    "gap analysis", "staffing gap", "forward staffing", "upcoming gaps",
    "90-day", "proactive", "forecast staffing", "predict gaps",
]


def _route_free_text(message: str, config: Optional[AgentConfig] = None) -> tuple[SubAgent, Optional[RoutingDecision]]:
    """Route a free-text message to the appropriate sub-agent.

    Strategy (layered, fast-to-slow):
      1. Deterministic keyword matching for high-signal phrases (instant)
      2. LLM router chain (Claude Haiku) for ambiguous messages (~200ms)
      3. Default to CHAT if LLM is unavailable or low-confidence

    Returns:
        Tuple of (SubAgent, optional RoutingDecision from LLM).
    """
    lower = message.lower().strip()

    # ── Layer 1: Deterministic keyword matching (instant) ─────────────
    if any(kw in lower for kw in _RERANKING_KEYWORDS):
        return SubAgent.RERANKING, None

    if any(kw in lower for kw in _FORWARD_STAFFING_KEYWORDS):
        return SubAgent.FORWARD_STAFFING, None

    # ── Layer 2: LLM router chain (Claude Haiku) ─────────────────────
    input_text = f"Type: USER MESSAGE\nMessage: {message}"
    decision = _invoke_llm_router(input_text, config)

    if decision is not None and decision.confidence >= _LLM_CONFIDENCE_THRESHOLD:
        # Map the LLM's sub_agent string to our SubAgent enum
        agent_map = {
            "reranking": SubAgent.RERANKING,
            "forward_staffing": SubAgent.FORWARD_STAFFING,
            "chat": SubAgent.CHAT,
            "event_dispatch": SubAgent.EVENT_DISPATCH,
        }
        sub_agent = agent_map.get(decision.sub_agent, SubAgent.CHAT)
        return sub_agent, decision

    # ── Layer 3: Default fallback ─────────────────────────────────────
    return SubAgent.CHAT, decision


def _route_event_payload(event_dict: dict[str, Any]) -> SubAgent:
    """Route an EventPayload dict to the appropriate sub-agent."""
    event_type = event_dict.get("event_type", "")
    agent = _EVENT_TYPE_TO_AGENT.get(event_type)
    if agent:
        return agent
    # Unknown event types default to event dispatch (will log warning)
    logger.warning("Unknown event_type '%s' — routing to event_dispatch", event_type)
    return SubAgent.EVENT_DISPATCH


# ---------------------------------------------------------------------------
# Sub-agent dispatch functions
# ---------------------------------------------------------------------------

def _dispatch_reranking(
    data: dict[str, Any],
    correlation_id: str,
    config: AgentConfig,
) -> dict[str, Any]:
    """Dispatch to the re-ranking chain sub-agent."""
    from agents.reranking_chain import rerank_candidates
    from agents.schemas import RoleRequirements, CandidateProfile

    role_data = data.get("role", {})
    candidates_data = data.get("candidates", [])
    preference_rules = data.get("preference_rules", [])
    dimension_weights = data.get("dimension_weights")

    role = RoleRequirements(**role_data)
    candidates = [CandidateProfile(**c) for c in candidates_data]

    result = rerank_candidates(
        role=role,
        candidates=candidates,
        preference_rules=preference_rules,
        dimension_weights=dimension_weights,
        config=config,
    )
    return result.model_dump() if hasattr(result, "model_dump") else result.dict()


def _dispatch_forward_staffing(
    data: dict[str, Any],
    correlation_id: str,
    config: AgentConfig,
) -> dict[str, Any]:
    """Dispatch to the forward staffing chain sub-agent."""
    from agents.forward_staffing_chain import (
        generate_gap_analysis_summary,
        generate_gap_recommendation,
    )

    action = data.get("action", "summary")

    if action == "recommendation":
        gap_data = data.get("gap_data", {})
        candidate_profiles = data.get("candidate_profiles", [])
        recommendation = generate_gap_recommendation(gap_data, candidate_profiles)
        return {"recommendation": recommendation, "action": "recommendation"}

    # Default: gap analysis summary
    gaps_data = data.get("gaps_data", [])
    available_tech_count = data.get("available_tech_count", 0)
    scan_date = data.get("scan_date", "")
    window_end = data.get("window_end", "")

    summary = generate_gap_analysis_summary(
        gaps_data=gaps_data,
        available_tech_count=available_tech_count,
        scan_date=scan_date,
        window_end=window_end,
    )
    return {"summary": summary, "action": "summary"}


def _dispatch_event(
    event_dict: dict[str, Any],
    correlation_id: str,
) -> dict[str, Any]:
    """Dispatch to the Celery reactive agent pipeline.

    Imports from the backend dispatcher to send the event through Celery.
    """
    import sys
    import os

    # Ensure backend is importable
    backend_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "backend")
    )
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    try:
        from app.workers.events import EventPayload
        from app.workers.dispatcher import dispatch_event

        # Inject correlation_id
        event_dict["correlation_id"] = correlation_id
        payload = EventPayload.from_dict(event_dict)
        task_ids = dispatch_event(payload)

        return {
            "dispatched": True,
            "event_type": event_dict.get("event_type"),
            "task_ids": task_ids,
            "correlation_id": correlation_id,
        }
    except ImportError:
        logger.error(
            "Backend dispatcher not importable — cannot dispatch event %s",
            event_dict.get("event_type"),
        )
        return {
            "dispatched": False,
            "event_type": event_dict.get("event_type"),
            "error": "Backend dispatcher not importable",
        }


def _dispatch_chat(
    message: str,
    correlation_id: str,
    context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Dispatch to the conversational chat sub-agent.

    Returns metadata for the caller; actual SSE streaming is handled by
    chat_service.py via the /api/chat endpoint. The orchestrator records
    the routing decision and correlation for audit purposes.
    """
    return {
        "routed_to": "chat_service",
        "message": message,
        "correlation_id": correlation_id,
        "context": context or {},
        "note": (
            "Chat messages are streamed via SSE through the /api/chat endpoint. "
            "The orchestrator records routing for audit trail only."
        ),
    }


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def _create_audit_entry(
    correlation_id: str,
    sub_agent: SubAgent,
    action: str,
    input_summary: str,
    result_summary: Optional[str] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    """Create a structured audit log entry."""
    return {
        "correlation_id": correlation_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sub_agent": sub_agent.value,
        "action": action,
        "input_summary": input_summary[:500],
        "result_summary": (result_summary[:500] if result_summary else None),
        "error": error,
    }


def _persist_audit(entries: list[dict[str, Any]]) -> None:
    """Best-effort persist audit entries to the database via AuditLog model.

    Uses a fresh DB session; failures are logged but do not propagate.
    """
    if not entries:
        return

    try:
        import sys
        import os

        backend_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "backend")
        )
        if backend_root not in sys.path:
            sys.path.insert(0, backend_root)

        from app.database import SessionLocal
        from app.models.audit import AuditLog

        session = SessionLocal()
        try:
            for entry in entries:
                log = AuditLog(
                    user_id="system",
                    action=f"orchestrator.{entry['action']}",
                    entity_type="orchestrator",
                    entity_id=entry["correlation_id"],
                    details=entry,
                    agent_name=f"orchestrator→{entry['sub_agent']}",
                )
                session.add(log)
            session.commit()
        except Exception:
            session.rollback()
            logger.warning("Failed to persist audit entries to DB")
        finally:
            session.close()
    except ImportError:
        logger.debug("DB not available for audit persistence — entries logged only")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def orchestrate(
    input: Union[str, dict[str, Any]],
    *,
    correlation_id: Optional[str] = None,
    context: Optional[dict[str, Any]] = None,
    config: Optional[AgentConfig] = None,
) -> OrchestratorResult:
    """Public entry point for the Deployable agent orchestration layer.

    Accepts either:
      - A free-text string (routed to chat, reranking, or forward staffing)
      - An EventPayload dict (routed to the reactive Celery pipeline)
      - A dict with ``{"message": "..."}`` for free-text via dict
      - A dict with ``{"event_type": "..."}`` for event dispatch
      - A dict with ``{"action": "rerank", ...}`` for direct sub-agent dispatch

    Args:
        input: The message or event to process.
        correlation_id: Optional correlation ID for tracing. Auto-generated if omitted.
        context: Optional context dict (session_id, user_role, etc.) passed to sub-agents.
        config: Optional AgentConfig override.

    Returns:
        OrchestratorResult with structured data, correlation tracking, and audit trail.

    Examples:
        >>> # Free-text chat message
        >>> result = orchestrate("Show me ready technicians in Texas")
        >>> result.sub_agent
        SubAgent.CHAT

        >>> # EventPayload dict
        >>> result = orchestrate({
        ...     "event_type": "cert.expired",
        ...     "entity_type": "technician_certification",
        ...     "entity_id": "cert-123",
        ...     "actor_id": "system",
        ... })
        >>> result.sub_agent
        SubAgent.EVENT_DISPATCH

        >>> # Direct reranking dispatch
        >>> result = orchestrate({
        ...     "action": "rerank",
        ...     "role": {...},
        ...     "candidates": [...],
        ... })
        >>> result.sub_agent
        SubAgent.RERANKING
    """
    start = time.time()
    cid = correlation_id or str(uuid.uuid4())
    cfg = config or load_agent_config()
    audit_entries: list[dict[str, Any]] = []

    # ── Determine input type and route ────────────────────────────────

    is_free_text = isinstance(input, str)
    is_event = isinstance(input, dict) and "event_type" in input
    is_direct_action = isinstance(input, dict) and "action" in input and not is_event
    is_dict_message = isinstance(input, dict) and "message" in input and not is_event

    # Route to sub-agent
    llm_decision: Optional[RoutingDecision] = None

    if is_free_text:
        message = input
        sub_agent, llm_decision = _route_free_text(message, cfg)
        input_summary = f"free_text: {message[:100]}"
    elif is_dict_message:
        message = input["message"]
        sub_agent, llm_decision = _route_free_text(message, cfg)
        input_summary = f"dict_message: {message[:100]}"
    elif is_direct_action:
        action = input["action"]
        if action == "rerank":
            sub_agent = SubAgent.RERANKING
        elif action in ("forward_staffing", "gap_analysis"):
            sub_agent = SubAgent.FORWARD_STAFFING
        else:
            sub_agent = SubAgent.CHAT
        input_summary = f"direct_action: {action}"
    elif is_event:
        sub_agent = _route_event_payload(input)
        input_summary = f"event: {input.get('event_type')}"
    else:
        # Fallback: treat as chat
        sub_agent = SubAgent.CHAT
        message = str(input)
        input_summary = f"unknown_input: {str(input)[:100]}"

    # Audit: routing decision (include LLM metadata when available)
    routing_meta = {}
    if llm_decision is not None:
        routing_meta = {
            "llm_routed": True,
            "llm_confidence": llm_decision.confidence,
            "llm_reasoning": llm_decision.reasoning,
            "llm_intent_category": llm_decision.intent_category,
        }
    else:
        routing_meta = {"llm_routed": False}

    audit_entries.append(
        _create_audit_entry(
            correlation_id=cid,
            sub_agent=sub_agent,
            action="routed",
            input_summary=input_summary,
            result_summary=str(routing_meta),
        )
    )

    logger.info(
        "Orchestrator routing [correlation=%s]: %s → %s (llm=%s)",
        cid,
        input_summary[:80],
        sub_agent.value,
        llm_decision is not None,
    )

    # ── Dispatch to sub-agent ─────────────────────────────────────────

    try:
        if sub_agent == SubAgent.RERANKING:
            data = input if isinstance(input, dict) else {}
            result_data = _dispatch_reranking(data, cid, cfg)
            status = "success"

        elif sub_agent == SubAgent.FORWARD_STAFFING:
            data = input if isinstance(input, dict) else {}
            result_data = _dispatch_forward_staffing(data, cid, cfg)
            status = "success"

        elif sub_agent == SubAgent.EVENT_DISPATCH:
            result_data = _dispatch_event(input, cid)
            status = "routed" if result_data.get("dispatched") else "error"

        elif sub_agent == SubAgent.CHAT:
            msg = message if (is_free_text or is_dict_message) else str(input)
            result_data = _dispatch_chat(msg, cid, context)
            status = "routed"

        else:
            result_data = {"error": "Unknown sub-agent"}
            status = "error"

    except Exception as e:
        logger.exception(
            "Orchestrator dispatch failed [correlation=%s, sub_agent=%s]: %s",
            cid,
            sub_agent.value,
            str(e),
        )
        elapsed = (time.time() - start) * 1000

        audit_entries.append(
            _create_audit_entry(
                correlation_id=cid,
                sub_agent=sub_agent,
                action="dispatch_error",
                input_summary=input_summary,
                error=str(e),
            )
        )

        # Persist audit (best-effort)
        _persist_audit(audit_entries)

        return OrchestratorResult(
            correlation_id=cid,
            sub_agent=sub_agent,
            status="error",
            data={},
            audit_entries=audit_entries,
            processing_time_ms=elapsed,
            error=str(e),
        )

    # ── Build result ──────────────────────────────────────────────────

    elapsed = (time.time() - start) * 1000

    audit_entries.append(
        _create_audit_entry(
            correlation_id=cid,
            sub_agent=sub_agent,
            action="completed",
            input_summary=input_summary,
            result_summary=str(result_data)[:500] if result_data else None,
        )
    )

    # Persist audit entries (best-effort, non-blocking)
    _persist_audit(audit_entries)

    result = OrchestratorResult(
        correlation_id=cid,
        sub_agent=sub_agent,
        status=status,
        data=result_data,
        audit_entries=audit_entries,
        processing_time_ms=elapsed,
    )

    logger.info(
        "Orchestrator complete [correlation=%s]: %s → %s in %.1fms",
        cid,
        sub_agent.value,
        status,
        elapsed,
    )

    return result
