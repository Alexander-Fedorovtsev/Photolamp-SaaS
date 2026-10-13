"""Small fail-closed Redis rate limiter for checkout mutations."""

import hashlib
from collections.abc import Awaitable
from typing import cast

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError

RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return {current, redis.call('TTL', KEYS[1])}
"""


async def enforce_rate_limit(
    redis: Redis,
    scope: str,
    *,
    limit: int,
    window_seconds: int,
) -> None:
    bucket = hashlib.sha256(scope.encode()).hexdigest()
    key = f"photolamp:rate:v1:{bucket}"
    try:
        result = await cast(
            Awaitable[list[int]],
            redis.eval(RATE_LIMIT_SCRIPT, 1, key, window_seconds),
        )
    except RedisError:
        raise HTTPException(status_code=503, detail="Checkout protection is unavailable") from None
    count, ttl = result
    if count > limit:
        retry_after = max(ttl, 1)
        raise HTTPException(
            status_code=429,
            detail="Too many checkout requests",
            headers={"Retry-After": str(retry_after)},
        )
