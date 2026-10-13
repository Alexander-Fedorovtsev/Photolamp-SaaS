import hashlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.bootstrap_runtime_role import RUNTIME_ROLE, validate_urls
from app.config import Settings
from app.main import create_app

USER_A = UUID("10000000-0000-0000-0000-000000000001")
USER_B = UUID("10000000-0000-0000-0000-000000000002")
USER_C = UUID("10000000-0000-0000-0000-000000000003")
TENANT_A = UUID("20000000-0000-0000-0000-000000000001")
TENANT_B = UUID("20000000-0000-0000-0000-000000000002")
TENANT_C = UUID("20000000-0000-0000-0000-000000000003")
SESSION_A = UUID("30000000-0000-0000-0000-000000000001")
SESSION_B = UUID("30000000-0000-0000-0000-000000000002")
SESSION_C = UUID("30000000-0000-0000-0000-000000000003")
TOKEN_A = "pl_" + "A" * 40
TOKEN_B = "pl_" + "B" * 40
TOKEN_C = "pl_" + "C" * 40


def urls() -> tuple[str, str]:
    defaults = Settings()
    owner = os.getenv("DATABASE_OWNER_URL", defaults.migration_database_url())
    configured = make_url(os.getenv("DATABASE_URL", defaults.database_url.get_secret_value()))
    runtime = configured.set(
        username=RUNTIME_ROLE,
        password="local-runtime-only",  # noqa: S106 - synthetic test credential
    )
    return owner, runtime.render_as_string(hide_password=False)


@pytest.fixture
async def tenant_database() -> AsyncIterator[tuple[str, str]]:
    owner_url, runtime_url = urls()
    owner = create_async_engine(owner_url)
    expires = datetime.now(UTC) + timedelta(hours=1)
    parameters = {
        "session_a": SESSION_A,
        "session_b": SESSION_B,
        "session_c": SESSION_C,
        "tenant_a": TENANT_A,
        "tenant_b": TENANT_B,
        "tenant_c": TENANT_C,
        "user_a": USER_A,
        "user_b": USER_B,
        "user_c": USER_C,
    }
    try:
        async with owner.begin() as connection:
            for statement in [
                "DELETE FROM auth_sessions WHERE id IN (:session_a, :session_b, :session_c)",
                "DELETE FROM tenant_memberships WHERE tenant_id IN (:tenant_a, :tenant_b)",
                "DELETE FROM tenants WHERE id IN (:tenant_a, :tenant_b, :tenant_c)",
                "DELETE FROM users WHERE id IN (:user_a, :user_b, :user_c)",
            ]:
                await connection.execute(text(statement), parameters)
            await connection.execute(
                text(
                    "INSERT INTO users (id, phone_e164) VALUES "
                    "(:user_a, '+19990000001'), (:user_b, '+19990000002'), "
                    "(:user_c, '+19990000003')"
                ),
                parameters,
            )
            await connection.execute(
                text(
                    "INSERT INTO tenants (id, slug, display_name, timezone) VALUES "
                    "(:tenant_a, 'synthetic-a', 'Synthetic A', 'Europe/Moscow'), "
                    "(:tenant_b, 'synthetic-b', 'Synthetic B', 'Asia/Yekaterinburg')"
                ),
                parameters,
            )
            await connection.execute(
                text(
                    "INSERT INTO tenant_memberships (tenant_id, user_id, role) VALUES "
                    "(:tenant_a, :user_a, 'owner'), (:tenant_b, :user_b, 'operator')"
                ),
                parameters,
            )
            await connection.execute(
                text(
                    "INSERT INTO auth_sessions (id, user_id, token_hash, expires_at) VALUES "
                    "(:session_a, :user_a, :hash_a, :expires), "
                    "(:session_b, :user_b, :hash_b, :expires), "
                    "(:session_c, :user_c, :hash_c, :expires)"
                ),
                {
                    **parameters,
                    "hash_a": hashlib.sha256(TOKEN_A.encode()).digest(),
                    "hash_b": hashlib.sha256(TOKEN_B.encode()).digest(),
                    "hash_c": hashlib.sha256(TOKEN_C.encode()).digest(),
                    "expires": expires,
                },
            )
        yield owner_url, runtime_url
    finally:
        async with owner.begin() as connection:
            for statement in [
                "DELETE FROM auth_sessions WHERE id IN (:session_a, :session_b, :session_c)",
                "DELETE FROM tenant_memberships WHERE tenant_id IN (:tenant_a, :tenant_b)",
                "DELETE FROM tenants WHERE id IN (:tenant_a, :tenant_b, :tenant_c)",
                "DELETE FROM users WHERE id IN (:user_a, :user_b, :user_c)",
            ]:
                await connection.execute(text(statement), parameters)
        await owner.dispose()


def test_runtime_url_must_use_a_distinct_fixed_role() -> None:
    owner = "postgresql+asyncpg://owner:owner-secret@db:5432/app"
    assert validate_urls(
        owner, "postgresql+asyncpg://photolamp_runtime:runtime-secret@db:5432/app"
    ) == (RUNTIME_ROLE, "runtime-secret")
    for invalid in [
        "postgresql+asyncpg://other:runtime-secret@db:5432/app",
        "postgresql+asyncpg://photolamp_runtime:owner-secret@db:5432/app",
        "postgresql+asyncpg://photolamp_runtime:runtime-secret@other:5432/app",
    ]:
        with pytest.raises(ValueError):
            validate_urls(owner, invalid)


@pytest.mark.integration
async def test_rls_denies_cross_tenant_crud_and_pool_scope_leak(
    tenant_database: tuple[str, str],
) -> None:
    owner_url, runtime_url = tenant_database
    owner = create_async_engine(owner_url)
    engine: AsyncEngine = create_async_engine(runtime_url, pool_size=1, max_overflow=0)
    try:
        async with owner.connect() as connection:
            role = (
                await connection.execute(
                    text(
                        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolbypassrls "
                        "FROM pg_roles WHERE rolname = :role"
                    ),
                    {"role": RUNTIME_ROLE},
                )
            ).one()
            assert tuple(role) == (False, False, False, False, False)
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(text("SELECT id FROM users"))
        async with engine.begin() as connection:
            assert (
                await connection.execute(text("SELECT current_user"))
            ).scalar_one() == RUNTIME_ROLE
            assert (await connection.execute(text("SELECT id FROM tenants"))).all() == []
            identity = (
                await connection.execute(
                    text("SELECT user_id, role FROM authorize_session_tenant(:hash, :tenant_id)"),
                    {
                        "hash": hashlib.sha256(TOKEN_A.encode()).digest(),
                        "tenant_id": TENANT_A,
                    },
                )
            ).one()
            assert identity == (USER_A, "owner")
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(TENANT_A)},
            )
            assert (await connection.execute(text("SELECT id FROM tenants"))).scalars().all() == [
                TENANT_A
            ]
            assert (
                await connection.execute(
                    text("UPDATE tenants SET display_name = 'blocked' WHERE id = :tenant_id"),
                    {"tenant_id": TENANT_B},
                )
            ).rowcount == 0
            assert (
                await connection.execute(
                    text("DELETE FROM tenant_memberships WHERE tenant_id = :tenant_id"),
                    {"tenant_id": TENANT_B},
                )
            ).rowcount == 0
        async with engine.begin() as connection:
            assert (await connection.execute(text("SELECT id FROM tenants"))).all() == []
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(TENANT_A)},
                )
                await connection.execute(
                    text(
                        "INSERT INTO tenants (id, slug, display_name, timezone) "
                        "VALUES (:id, 'cross-tenant', 'Blocked', 'UTC')"
                    ),
                    {"id": TENANT_C},
                )
    finally:
        await owner.dispose()
        await engine.dispose()


@pytest.mark.integration
async def test_http_session_cannot_switch_tenant_with_url_or_header(
    tenant_database: tuple[str, str],
) -> None:
    owner_url, runtime_url = tenant_database
    app = create_app(Settings(app_env="test", database_url=runtime_url, enable_demo_previews=True))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/me/tenants")).status_code == 401
            headers = {"Authorization": f"Bearer {TOKEN_A}", "X-Tenant-ID": str(TENANT_B)}
            memberships = await client.get("/api/v1/me/tenants", headers=headers)
            assert memberships.status_code == 200
            assert [item["tenant_id"] for item in memberships.json()] == [str(TENANT_A)]
            own = await client.get(f"/api/v1/t/{TENANT_A}/context", headers=headers)
            assert own.status_code == 200
            assert own.json()["role"] == "owner"
            assert own.json()["tenant_id"] == str(TENANT_A)
            foreign = await client.get(f"/api/v1/t/{TENANT_B}/context", headers=headers)
            assert foreign.status_code == 404
            invalid = await client.get(
                "/api/v1/me/tenants", headers={"Authorization": "Bearer too-short"}
            )
            assert invalid.status_code == 401
            unknown = await client.get(
                "/api/v1/me/tenants",
                headers={"Authorization": "Bearer pl_" + "Z" * 40},
            )
            assert unknown.status_code == 401
            empty = await client.get(
                "/api/v1/me/tenants",
                headers={"Authorization": f"Bearer {TOKEN_C}"},
            )
            assert empty.status_code == 200
            assert empty.json() == []

            owner = create_async_engine(owner_url)
            try:
                async with owner.begin() as connection:
                    await connection.execute(
                        text("UPDATE auth_sessions SET revoked_at = now() WHERE id = :session_id"),
                        {"session_id": SESSION_A},
                    )
                revoked = await client.get("/api/v1/me/tenants", headers=headers)
                assert revoked.status_code == 401
            finally:
                await owner.dispose()
