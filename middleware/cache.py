"""LLM response cache backed by Valkey/Redis.

Fails *open* (cache miss, not an error) if Valkey is unreachable -- same
resilience posture as middleware/rate_limit.py. Callers must only ever pass
already-masked text in: this cache stores whatever string it's given
verbatim, so caching raw PII would be a real leak. sql_generator and
response_formatter both cache post-masking content, never raw rows/queries.
"""
import hashlib
import logging
import os

import redis

logger = logging.getLogger("llm_cache")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DEFAULT_TTL_SECONDS = 3600

_client: "redis.Redis | None" = None
_unavailable = False


def _get_client() -> "redis.Redis | None":
    global _client, _unavailable
    if _unavailable:
        return None
    if _client is None:
        _client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5)
    return _client


def make_key(*parts: str) -> str:
    """Builds a cache key from masked/non-sensitive parts (e.g. masked
    question + schema fingerprint + model name)."""
    joined = "|".join(parts)
    return "llmcache:" + hashlib.sha256(joined.encode()).hexdigest()


def get(key: str) -> str | None:
    client = _get_client()
    if client is None:
        return None
    try:
        value = client.get(key)
        return value.decode() if value is not None else None
    except redis.RedisError as e:
        _mark_unavailable(e)
        return None


def set(key: str, value: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        client.setex(key, ttl_seconds, value)
    except redis.RedisError as e:
        _mark_unavailable(e)


def _mark_unavailable(e: Exception) -> None:
    global _unavailable
    _unavailable = True
    logger.warning("llm_cache_backend_unavailable", extra={"error": str(e)})
