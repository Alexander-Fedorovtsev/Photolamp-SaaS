from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.config import Settings
from app.database import create_engine
from app.health import Readiness, check_readiness
from app.previews import DEMO_TENANTS, PreviewTenant


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
            "Foundation only. Demo previews are synthetic; "
            "booking, SMS and payments are unavailable."
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

    @application.get("/api/v1/r/{tenant_slug}/preview", tags=["demo"], response_model=PreviewTenant)
    async def preview(tenant_slug: str) -> PreviewTenant:
        tenant = DEMO_TENANTS.get(tenant_slug) if config.enable_demo_previews else None
        if tenant is None:
            raise HTTPException(status_code=404, detail="Preview not found")
        return tenant

    return application


app = create_app()
