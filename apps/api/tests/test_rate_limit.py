from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError

from app.rate_limit import enforce_rate_limit


async def test_checkout_rate_limit_fails_closed_when_redis_is_unavailable() -> None:
    redis = AsyncMock()
    redis.eval.side_effect = RedisError("synthetic outage")

    with pytest.raises(HTTPException) as raised:
        await enforce_rate_limit(redis, "synthetic-scope", limit=10, window_seconds=60)

    assert raised.value.status_code == 503
    assert raised.value.detail == "Checkout protection is unavailable"
