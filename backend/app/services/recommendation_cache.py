"""Redis caching layer for recommendation engine results.

Provides fast read access to pre-computed next-step and suggested-action
recommendations without hitting PostgreSQL on every request. Cache is
invalidated when recommendations are regenerated (nightly batch or event-triggered).

Cache key patterns:
    next_steps:{technician_id}         → JSON list of next-step recommendations
    suggested_actions:{role}:{user_id} → JSON list of suggested actions
    next_steps:all_tech_ids            → JSON set of technician IDs with cached recs
    rec_cache:last_refresh             → ISO timestamp of last full refresh
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import redis

logger = logging.getLogger("deployable.recommendation_cache")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Cache TTL: 25 hours (slightly longer than nightly cycle to avoid gaps)
CACHE_TTL_SECONDS = 25 * 60 * 60

# Cache key prefixes
NEXT_STEPS_PREFIX = "rec_cache:next_steps"
SUGGESTED_ACTIONS_PREFIX = "rec_cache:suggested_actions"
LAST_REFRESH_KEY = "rec_cache:last_refresh"
ALL_TECH_IDS_KEY = "rec_cache:all_tech_ids"

_redis_client: Optional[redis.Redis] = None


def _get_redis() -> redis.Redis:
    """Get or create a Redis client for caching."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def _safe_redis(func):
    """Decorator that swallows Redis errors and returns fallback."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            logger.warning("Redis cache operation failed: %s", func.__name__, exc_info=True)
            return kwargs.get("fallback", None)
    return wrapper


# ---------------------------------------------------------------------------
# Next-step recommendation caching
# ---------------------------------------------------------------------------

def cache_next_steps(technician_id: str, recommendations: list[dict[str, Any]]) -> bool:
    """Cache next-step recommendations for a technician.

    Args:
        technician_id: UUID string of the technician
        recommendations: List of recommendation dicts to cache

    Returns:
        True if cached successfully
    """
    try:
        r = _get_redis()
        key = f"{NEXT_STEPS_PREFIX}:{technician_id}"
        payload = json.dumps(recommendations, default=str)
        r.setex(key, CACHE_TTL_SECONDS, payload)

        # Track this technician in the index set
        r.sadd(ALL_TECH_IDS_KEY, technician_id)
        r.expire(ALL_TECH_IDS_KEY, CACHE_TTL_SECONDS)

        return True
    except Exception:
        logger.warning("Failed to cache next steps for tech %s", technician_id, exc_info=True)
        return False


def get_cached_next_steps(technician_id: str) -> Optional[list[dict[str, Any]]]:
    """Retrieve cached next-step recommendations for a technician.

    Returns:
        List of recommendation dicts, or None if cache miss
    """
    try:
        r = _get_redis()
        key = f"{NEXT_STEPS_PREFIX}:{technician_id}"
        data = r.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception:
        logger.warning("Failed to read next steps cache for tech %s", technician_id, exc_info=True)
        return None


def invalidate_next_steps(technician_id: str) -> bool:
    """Invalidate cached next-step recommendations for a single technician."""
    try:
        r = _get_redis()
        key = f"{NEXT_STEPS_PREFIX}:{technician_id}"
        r.delete(key)
        return True
    except Exception:
        logger.warning("Failed to invalidate next steps cache for tech %s", technician_id, exc_info=True)
        return False


def invalidate_all_next_steps() -> int:
    """Invalidate all cached next-step recommendations (for nightly refresh)."""
    try:
        r = _get_redis()
        tech_ids = r.smembers(ALL_TECH_IDS_KEY)
        if tech_ids:
            keys = [f"{NEXT_STEPS_PREFIX}:{tid}" for tid in tech_ids]
            r.delete(*keys)
            r.delete(ALL_TECH_IDS_KEY)
            return len(keys)
        return 0
    except Exception:
        logger.warning("Failed to invalidate all next steps cache", exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Suggested actions caching
# ---------------------------------------------------------------------------

def cache_suggested_actions(
    role: str,
    user_id: Optional[str],
    actions: list[dict[str, Any]],
) -> bool:
    """Cache suggested actions for a role/user combination.

    Args:
        role: Target role (ops, technician, partner)
        user_id: Optional specific user ID (None for role-wide actions)
        actions: List of action dicts to cache
    """
    try:
        r = _get_redis()
        user_part = user_id or "all"
        key = f"{SUGGESTED_ACTIONS_PREFIX}:{role}:{user_part}"
        payload = json.dumps(actions, default=str)
        r.setex(key, CACHE_TTL_SECONDS, payload)
        return True
    except Exception:
        logger.warning("Failed to cache suggested actions for %s/%s", role, user_id, exc_info=True)
        return False


def get_cached_suggested_actions(
    role: str,
    user_id: Optional[str] = None,
) -> Optional[list[dict[str, Any]]]:
    """Retrieve cached suggested actions for a role/user."""
    try:
        r = _get_redis()
        user_part = user_id or "all"
        key = f"{SUGGESTED_ACTIONS_PREFIX}:{role}:{user_part}"
        data = r.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception:
        logger.warning("Failed to read suggested actions cache for %s/%s", role, user_id, exc_info=True)
        return None


def invalidate_suggested_actions(role: Optional[str] = None) -> bool:
    """Invalidate suggested action caches.

    Args:
        role: If provided, invalidate only for this role. Otherwise invalidate all.
    """
    try:
        r = _get_redis()
        if role:
            # Use SCAN to find matching keys
            pattern = f"{SUGGESTED_ACTIONS_PREFIX}:{role}:*"
        else:
            pattern = f"{SUGGESTED_ACTIONS_PREFIX}:*"

        cursor = 0
        deleted = 0
        while True:
            cursor, keys = r.scan(cursor, match=pattern, count=100)
            if keys:
                r.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break

        return True
    except Exception:
        logger.warning("Failed to invalidate suggested actions cache", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def set_last_refresh_timestamp() -> bool:
    """Record when the recommendation cache was last fully refreshed."""
    try:
        r = _get_redis()
        now = datetime.now(timezone.utc).isoformat()
        r.setex(LAST_REFRESH_KEY, CACHE_TTL_SECONDS, now)
        return True
    except Exception:
        logger.warning("Failed to set last refresh timestamp", exc_info=True)
        return False


def get_last_refresh_timestamp() -> Optional[str]:
    """Get the ISO timestamp of the last full cache refresh."""
    try:
        r = _get_redis()
        return r.get(LAST_REFRESH_KEY)
    except Exception:
        return None
