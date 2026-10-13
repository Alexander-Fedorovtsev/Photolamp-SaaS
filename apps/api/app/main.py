import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError

from app.admin_routes import router as admin_router
from app.auth import DemoSession, SessionRevoked, issue_demo_session, revoke_session
from app.availability import Availability, Quote, QuoteRequest, calculate_quote, check_availability
from app.catalog import PreviewTenant, PublicCatalog, load_public_catalog, preview_from_catalog
from app.config import Settings
from app.database import Database, create_engine
from app.health import Readiness, check_readiness
from app.holds import ReleasedHold, RentalHold, create_hold, release_hold
from app.tenancy import (
    TenantContext,
    TenantSummary,
    database,
    get_tenant_context,
    list_memberships,
    session_digest,
)

logger = logging.getLogger(__name__)


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
            "Tenant/RLS foundation with an auditable catalog and date-aware quote preview. "
            "Synthetic demo staff access is environment-gated. Short-lived inventory holds are "
            "available only for explicitly enabled storefronts; orders, SMS and payments are "
            "unavailable."
        ),
    )
    application.include_router(admin_router)

    @application.exception_handler(IntegrityError)
    async def integrity_conflict(_request: object, _error: IntegrityError) -> JSONResponse:
        logger.warning("Catalog integrity conflict: %s", _error.orig)
        return JSONResponse(
            {"detail": "Catalog data conflicts with an existing or related record"},
            status_code=409,
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
        "/api/v1/r/{tenant_slug}/availability",
        tags=["storefront"],
        response_model=Availability,
    )
    async def public_availability(
        tenant_slug: str,
        model_id: UUID,
        branch_id: UUID,
        start_date: date,
        end_date: date,
        response: Response,
        db: Annotated[Database, Depends(database)],
    ) -> Availability:
        response.headers["Cache-Control"] = "no-store"
        request = QuoteRequest(
            model_id=model_id,
            branch_id=branch_id,
            start_date=start_date,
            end_date=end_date,
            fulfillment="pickup",
        )
        return await check_availability(
            db, tenant_slug, request, allow_demo=config.enable_demo_previews
        )

    @application.post(
        "/api/v1/r/{tenant_slug}/checkout/quote",
        tags=["storefront"],
        response_model=Quote,
    )
    async def public_quote(
        tenant_slug: str,
        command: QuoteRequest,
        response: Response,
        db: Annotated[Database, Depends(database)],
    ) -> Quote:
        response.headers["Cache-Control"] = "no-store"
        return await calculate_quote(
            db, tenant_slug, command, allow_demo=config.enable_demo_previews
        )

    @application.post(
        "/api/v1/r/{tenant_slug}/checkout/holds",
        tags=["storefront"],
        response_model=RentalHold,
        status_code=201,
    )
    async def public_hold(
        tenant_slug: str,
        command: QuoteRequest,
        response: Response,
        db: Annotated[Database, Depends(database)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=16, max_length=128)
        ],
    ) -> RentalHold:
        response.headers["Cache-Control"] = "no-store"
        return await create_hold(
            db,
            tenant_slug,
            command,
            idempotency_key,
            allow_demo=config.enable_demo_previews,
            ttl_seconds=config.hold_ttl_seconds,
        )

    @application.delete(
        "/api/v1/r/{tenant_slug}/checkout/holds/{hold_id}",
        tags=["storefront"],
        response_model=ReleasedHold,
    )
    async def public_release_hold(
        tenant_slug: str,
        hold_id: UUID,
        response: Response,
        db: Annotated[Database, Depends(database)],
        management_token: Annotated[
            str, Header(alias="X-Hold-Token", min_length=32, max_length=128)
        ],
    ) -> ReleasedHold:
        response.headers["Cache-Control"] = "no-store"
        return await release_hold(
            db,
            tenant_slug,
            hold_id,
            management_token,
            allow_demo=config.enable_demo_previews,
        )

    @application.post(
        "/api/v1/auth/demo-session",
        tags=["authentication"],
        response_model=DemoSession,
    )
    async def demo_session(
        response: Response,
        db: Annotated[Database, Depends(database)],
    ) -> DemoSession:
        if not config.enable_demo_previews or config.app_env == "production":
            raise HTTPException(status_code=404, detail="Demo session unavailable")
        response.headers["Cache-Control"] = "no-store"
        return await issue_demo_session(db)

    @application.delete(
        "/api/v1/auth/session",
        tags=["authentication"],
        response_model=SessionRevoked,
    )
    async def logout(
        digest: Annotated[bytes, Depends(session_digest)],
        db: Annotated[Database, Depends(database)],
    ) -> SessionRevoked:
        return await revoke_session(db, digest)

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
