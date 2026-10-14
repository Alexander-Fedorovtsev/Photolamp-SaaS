from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
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
            await session.execute(
                text("SELECT touch_auth_session(:token_hash)"), {"token_hash": token_hash}
            )
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

    async def issue_demo_session(
        self, session_id: UUID, token_hash: bytes, expires_at: datetime, client_label: str
    ) -> UUID:
        async with self.transaction() as session:
            value = await session.scalar(
                text("SELECT issue_demo_staff_session(:session_id, :token_hash, :expires_at)"),
                {
                    "session_id": session_id,
                    "token_hash": token_hash,
                    "expires_at": expires_at,
                },
            )
            annotated = await session.scalar(
                text(
                    "SELECT COALESCE(set_auth_session_metadata("
                    ":token_hash, :client_label, 'demo'), false)"
                ),
                {"token_hash": token_hash, "client_label": client_label},
            )
            if not annotated:
                raise RuntimeError("Issued demo session could not be annotated")
            return UUID(str(value))

    async def revoke_session(self, token_hash: bytes) -> bool:
        async with self.transaction() as session:
            return bool(
                await session.scalar(
                    text("SELECT COALESCE(revoke_session(:token_hash), false)"),
                    {"token_hash": token_hash},
                )
            )

    @asynccontextmanager
    async def public_tenant_transaction(
        self, slug: str, *, allow_demo: bool
    ) -> AsyncIterator["PublicTenantTransaction"]:
        async with self.transaction() as session:
            storefront = (
                await session.execute(
                    text("SELECT tenant_id, is_demo FROM resolve_public_storefront(:slug)"),
                    {"slug": slug},
                )
            ).one_or_none()
            if storefront is None or (storefront.is_demo and not allow_demo):
                raise PublicStorefrontNotFound
            tenant_id = UUID(str(storefront.tenant_id))
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            yield PublicTenantTransaction(
                session=session,
                tenant_id=tenant_id,
                is_demo=bool(storefront.is_demo),
            )

    @asynccontextmanager
    async def tenant_transaction(
        self, token_hash: bytes, tenant_id: UUID
    ) -> AsyncIterator["TenantTransaction"]:
        async with self.transaction() as session:
            await session.execute(
                text("SELECT touch_auth_session(:token_hash)"), {"token_hash": token_hash}
            )
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


class PublicStorefrontNotFound(Exception):
    """The slug is not a published storefront in the current environment."""


@dataclass(frozen=True, slots=True)
class TenantTransaction:
    session: AsyncSession
    tenant_id: UUID
    user_id: UUID
    role: str


@dataclass(frozen=True, slots=True)
class PublicTenantTransaction:
    session: AsyncSession
    tenant_id: UUID
    is_demo: bool
