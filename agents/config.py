"""
Agent configuration — model tiers, API keys, and defaults.

Model tier strategy (from seed.yaml constraints):
  - Claude Haiku: background agents, routing, scoring (cost-efficient)
  - Claude Sonnet: conversational, NL parsing, complex reasoning
"""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentConfig:
    """Immutable configuration for the agent layer."""

    # Anthropic API key — required for LLM calls
    anthropic_api_key: str = ""

    # Model tiers
    haiku_model: str = "claude-haiku-4-20250514"
    sonnet_model: str = "claude-sonnet-4-20250514"

    # Re-ranking chain uses Haiku (background agent, cost-efficient)
    reranking_model: str = "claude-haiku-4-20250514"

    # Temperature for scoring (low = deterministic)
    reranking_temperature: float = 0.1

    # Max tokens for re-ranking response
    reranking_max_tokens: int = 4096

    # How many candidates to accept in re-ranking shortlist
    shortlist_size: int = 20

    # Top-N to return after re-ranking
    top_n_results: int = 10

    # Dimension weights (must sum to 1.0)
    dimension_weights: dict = field(default_factory=lambda: {
        "skill_match": 0.30,
        "proximity": 0.15,
        "availability": 0.20,
        "cost_efficiency": 0.15,
        "past_performance": 0.20,
    })


def load_agent_config() -> AgentConfig:
    """Load agent config from environment variables with sensible defaults."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")

    # Allow overriding model names via env
    reranking_model = os.getenv("RERANKING_MODEL", AgentConfig.reranking_model)

    return AgentConfig(
        anthropic_api_key=api_key,
        reranking_model=reranking_model,
    )
