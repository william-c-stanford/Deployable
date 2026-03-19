"""
Deployable Agents — LangChain-based autonomous workforce intelligence agents.

This package contains the agent orchestration layer including:
- Staffing recommendation re-ranking chain
- Background scoring agents
- Conversational chat agent (future)
"""

from agents.reranking_chain import build_reranking_chain, rerank_candidates
from agents.orchestrator import (
    orchestrate,
    OrchestratorResult,
    SubAgent,
    RoutingDecision,
    SubAgentRegistry,
    SubAgentDescriptor,
    AgentTier,
    AgentKind,
    get_registry,
    reset_registry,
)

__all__ = [
    "build_reranking_chain",
    "rerank_candidates",
    "orchestrate",
    "OrchestratorResult",
    "SubAgent",
    "RoutingDecision",
    "SubAgentRegistry",
    "SubAgentDescriptor",
    "AgentTier",
    "AgentKind",
    "get_registry",
    "reset_registry",
]
