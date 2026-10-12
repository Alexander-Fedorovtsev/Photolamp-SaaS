import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.database import create_engine
from app.health import check_database, check_readiness


async def test_readiness_bounds_slow_dependencies() -> None:
    async def slow(_: AsyncEngine) -> None:
        await asyncio.sleep(60)

    with patch("app.health.check_database", new=slow):
        result = await check_readiness(AsyncMock(spec=AsyncEngine), AsyncMock(spec=Redis), 0.01)
    assert result.status == "not_ready"
    assert result.checks["database"] == "unavailable"


async def test_database_rejects_stale_migrations() -> None:
    engine = AsyncMock(spec=AsyncEngine)
    connection = engine.connect.return_value.__aenter__.return_value
    connection.execute = AsyncMock(return_value=MagicMock())
    connection.execute.return_value.scalars.return_value = ["old_revision"]
    with pytest.raises(RuntimeError, match="migrations are not current"):
        await check_database(engine)


@pytest.mark.integration
async def test_real_dependencies_and_current_migration() -> None:
    settings = Settings()
    engine = create_engine(settings)
    redis = Redis.from_url(settings.redis_url.get_secret_value())
    try:
        result = await check_readiness(engine, redis, 2)
        assert result.status == "ready"
        assert result.checks == {"database": "ok", "redis": "ok"}
    finally:
        await engine.dispose()
        await redis.aclose()
