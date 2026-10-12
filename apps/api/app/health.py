import asyncio
from collections.abc import Awaitable
from pathlib import Path
from typing import Literal, cast

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import ScalarResult, text
from sqlalchemy.ext.asyncio import AsyncEngine


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, Literal["ok", "unavailable"]]


def migration_head() -> str | None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


async def check_database(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        versions: ScalarResult[str] = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalars()
        if list(versions) != [migration_head()]:
            raise RuntimeError("Database migrations are not current")


async def check_readiness(engine: AsyncEngine, redis: Redis, timeout_seconds: float) -> Readiness:
    async def probe(name: str) -> Literal["ok", "unavailable"]:
        try:
            async with asyncio.timeout(timeout_seconds):
                if name == "database":
                    await check_database(engine)
                elif not await cast(Awaitable[bool], redis.ping()):
                    return "unavailable"
            return "ok"
        except Exception:
            # Public probes never disclose connection strings or exception details.
            return "unavailable"

    names = ("database", "redis")
    values = await asyncio.gather(*(probe(name) for name in names))
    checks = dict(zip(names, values, strict=True))
    return Readiness(
        status="ready" if all(value == "ok" for value in values) else "not_ready", checks=checks
    )
