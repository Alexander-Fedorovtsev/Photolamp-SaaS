"""Create or rotate the least-privilege PostgreSQL runtime login."""

import asyncio

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings

RUNTIME_ROLE = "photolamp_runtime"


def validate_urls(owner_url: str, runtime_url: str) -> tuple[str, str]:
    owner = make_url(owner_url)
    runtime = make_url(runtime_url)
    if runtime.username != RUNTIME_ROLE:
        raise ValueError(f"Runtime database user must be {RUNTIME_ROLE}")
    if not runtime.password or runtime.password == owner.password:
        raise ValueError("Runtime database password must be configured separately")
    if (runtime.host, runtime.port, runtime.database) != (
        owner.host,
        owner.port,
        owner.database,
    ):
        raise ValueError("Owner and runtime URLs must address the same database")
    return RUNTIME_ROLE, runtime.password


async def bootstrap() -> None:
    settings = Settings()
    if settings.database_owner_url is None:
        raise RuntimeError("DATABASE_OWNER_URL is required for database bootstrap")
    owner_url = settings.database_owner_url.get_secret_value()
    role, password = validate_urls(owner_url, settings.database_url.get_secret_value())
    engine = create_async_engine(owner_url)
    try:
        async with engine.begin() as connection:
            statement: str = (
                await connection.execute(
                    text(
                        "SELECT format("
                        "'ALTER ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOINHERIT NOBYPASSRLS', "
                        "CAST(:role AS text), CAST(:password AS text))"
                    ),
                    {"role": role, "password": password},
                )
            ).scalar_one()
            exists: bool = (
                await connection.execute(
                    text("SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = :role)"),
                    {"role": role},
                )
            ).scalar_one()
            if not exists:
                statement = statement.replace("ALTER ROLE", "CREATE ROLE", 1)
            await connection.execute(text(statement))
            grant: str = (
                await connection.execute(
                    text(
                        "SELECT format('GRANT CONNECT ON DATABASE %I TO %I', "
                        "current_database(), CAST(:role AS text))"
                    ),
                    {"role": role},
                )
            ).scalar_one()
            await connection.execute(text(grant))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(bootstrap())
