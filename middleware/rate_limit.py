"""Per-user rate limiting backed by Valkey/Redis (fixed-window counter).

Fails *open* if Valkey is unreachable -- a rate limiter that takes the app
down when its backing store is unavailable is worse than no rate limiter,
so an infra outage here degrades to "unlimited" rather than "broken".
"""
import logging
import os
import time

import redis

logger = logging.getLogger("rate_limit")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

_client: "redis.Redis | None" = None
_unavailable = False


def _get_client() -> "redis.Redis | None":
    global _client, _unavailable
    if _unavailable:
        return None
    if _client is None:
        _client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5)
    return _client


def check_rate_limit(key: str, limit_per_minute: int) -> tuple[bool, int]:
    """Returns (allowed, current_count) for `key` in the current 60s window.

    Anonymous/no-op keys (empty string) are never limited -- rate limiting
    only makes sense once there's an identity to attribute requests to.
    """
    if not key or limit_per_minute <= 0:
        return True, 0

    client = _get_client()
    if client is None:
        return True, 0

    window = int(time.time() // 60)
    redis_key = f"ratelimit:{key}:{window}"
    try:
        count = client.incr(redis_key)
        if count == 1:
            client.expire(redis_key, 60)
        return count <= limit_per_minute, count
    except redis.RedisError as e:
        global _unavailable
        _unavailable = True
        logger.warning("rate_limit_backend_unavailable", extra={"error": str(e)})
        return True, 0
