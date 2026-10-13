from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.catalog import PreviewTenant, PublicCatalog, load_public_catalog, preview_from_catalog
from app.config import Settings
from app.database import Database, create_engine
from app.health import Readiness, check_readiness
from app.tenancy import (
    TenantContext,
    TenantSummary,
    database,
    get_tenant_context,
    list_memberships,
    session_digest,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(config)
        redis = Redis.from_url(
            config.redis_url.get_secret_value(),
            socket_connect_timeout=config.dependency_timeout_seconds,
            socket_timeout=config.dependency_timeout_seconds,
        )
        application.state.engine = engine
        application.state.database = Database(engine)
        application.state.redis = redis
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    application = FastAPI(
        title="Photolamp SaaS API",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Tenant/RLS foundation with a synthetic database-backed catalog. "
            "Session issuance, booking, SMS and payments are unavailable."
        ),
    )

    @application.get("/api/v1/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @application.get("/api/v1/version", tags=["health"])
    async def version() -> JSONResponse:
        return JSONResponse(
            {"git_sha": config.photolamp_deploy_sha}, headers={"Cache-Control": "no-store"}
        )

    @application.get(
        "/api/v1/health/ready",
        tags=["health"],
        response_model=Readiness,
        responses={503: {"model": Readiness}},
    )
    async def ready() -> JSONResponse:
        result = await check_readiness(
            application.state.engine,
            application.state.redis,
            config.dependency_timeout_seconds,
        )
        return JSONResponse(
            result.model_dump(),
            status_code=200 if result.status == "ready" else 503,
            headers={"Cache-Control": "no-store"},
        )

    @application.get(
        "/api/v1/r/{tenant_slug}/catalog",
        tags=["storefront"],
        response_model=PublicCatalog,
    )
    async def public_catalog(
        tenant_slug: str,
        response: Response,
        db: Annotated[Database, Depends(database)],
    ) -> PublicCatalog:
        response.headers["Cache-Control"] = "no-store"
        return await load_public_catalog(db, tenant_slug, allow_demo=config.enable_demo_previews)

    @application.get(
        "/api/v1/r/{tenant_slug}/preview",
        tags=["storefront"],
        response_model=PreviewTenant,
        deprecated=True,
    )
    async def preview(
        tenant_slug: str,
        response: Response,
        db: Annotated[Database, Depends(database)],
    ) -> PreviewTenant:
        response.headers["Cache-Control"] = "no-store"
        catalog = await load_public_catalog(db, tenant_slug, allow_demo=config.enable_demo_previews)
        return preview_from_catalog(catalog)

    @application.get(
        "/api/v1/me/tenants",
        tags=["tenancy"],
        response_model=list[TenantSummary],
    )
    async def my_tenants(
        digest: Annotated[bytes, Depends(session_digest)],
        db: Annotated[Database, Depends(database)],
    ) -> list[TenantSummary]:
        if await db.session_user(digest) is None:
            raise HTTPException(
                status_code=401,
                detail="Valid session required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await list_memberships(db, digest)

    @application.get(
        "/api/v1/t/{tenant_id}/context",
        tags=["tenancy"],
        response_model=TenantContext,
    )
    async def tenant_context(
        tenant_id: UUID,
        digest: Annotated[bytes, Depends(session_digest)],
        db: Annotated[Database, Depends(database)],
    ) -> TenantContext:
        return await get_tenant_context(db, digest, tenant_id)

    return application


app = create_app()
