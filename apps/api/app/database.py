from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"timeout": settings.dependency_timeout_seconds},
    )


class Database:
    """Infrastructure only. Tenant repositories must require an authorized scope."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions.begin() as session:
            yield session

    async def list_tenants(self, token_hash: bytes) -> list[dict[str, object]]:
        async with self.transaction() as session:
            result = await session.execute(
                text(
                    "SELECT tenant_id, slug, display_name, timezone, role "
                    "FROM list_session_tenants(:token_hash)"
                ),
                {"token_hash": token_hash},
            )
            return [dict(row._mapping) for row in result]

    async def session_user(self, token_hash: bytes) -> UUID | None:
        async with self.transaction() as session:
            value = await session.scalar(
                text("SELECT resolve_session_user(:token_hash)"),
                {"token_hash": token_hash},
            )
            return None if value is None else UUID(str(value))

    @asynccontextmanager
    async def tenant_transaction(
        self, token_hash: bytes, tenant_id: UUID
    ) -> AsyncIterator["TenantTransaction"]:
        async with self.transaction() as session:
            authorized = (
                await session.execute(
                    text(
                        "SELECT user_id, role FROM "
                        "authorize_session_tenant(:token_hash, :tenant_id)"
                    ),
                    {"token_hash": token_hash, "tenant_id": tenant_id},
                )
            ).one_or_none()
            if authorized is None:
                raise TenantAccessDenied
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            yield TenantTransaction(
                session=session,
                tenant_id=tenant_id,
                user_id=authorized.user_id,
                role=authorized.role,
            )


class TenantAccessDenied(Exception):
    """The authenticated session has no active membership for this tenant."""


@dataclass(frozen=True, slots=True)
class TenantTransaction:
    session: AsyncSession
    tenant_id: UUID
    user_id: UUID
    role: str
