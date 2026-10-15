import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError

from app.admin_dashboard_routes import router as admin_dashboard_router
from app.admin_order_routes import router as admin_order_router
from app.admin_routes import router as admin_router
from app.admin_team_routes import router as admin_team_router
from app.auth import DemoSession, SessionRevoked, issue_demo_session, revoke_session
from app.availability import Availability, Quote, QuoteRequest, calculate_quote, check_availability
from app.catalog import PreviewTenant, PublicCatalog, load_public_catalog, preview_from_catalog
from app.checkout import CheckoutSession, create_checkout_session
from app.config import Settings
from app.contact_crypto import ContactCipher
from app.courier_routes import router as courier_router
from app.customer_auth import (
    CustomerIdentity,
    CustomerSession,
    OtpChallenge,
    OtpRequest,
    OtpVerification,
    customer_session_digest,
    load_customer_identity,
    request_customer_otp,
    verify_customer_otp,
)
from app.database import Database, create_engine
from app.health import Readiness, check_readiness
from app.holds import ReleasedHold, RentalHold, create_hold, release_hold
from app.observability import (
    HttpMetrics,
    ObservabilityMiddleware,
    configure_logging,
    metrics_response,
)
from app.order_requests import (
    ExtensionRequestCommand,
    ExtensionRequestResult,
    ReturnRequestCommand,
    ReturnRequestResult,
    create_extension_request,
    create_return_request,
)
from app.orders import (
    CustomerOrder,
    CustomerOrderPage,
    DraftOrder,
    DraftOrderRequest,
    create_draft_order,
    list_customer_orders,
    load_customer_order,
    load_draft_order,
)
from app.rate_limit import enforce_rate_limit
from app.sms import build_sms_provider
from app.staff_auth_routes import router as staff_auth_router
from app.staff_session_routes import router as staff_session_router
from app.staff_sessions import coarse_client_label
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
    metrics = HttpMetrics(
        environment=config.app_env,
        revision=config.photolamp_deploy_sha,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(config.app_env, config.photolamp_deploy_sha)
        engine = create_engine(config)
        redis = Redis.from_url(
            config.redis_url.get_secret_value(),
            socket_connect_timeout=config.dependency_timeout_seconds,
            socket_timeout=config.dependency_timeout_seconds,
        )
        application.state.engine = engine
        application.state.config = config
        application.state.contact_cipher = ContactCipher(
            config.contact_keyring(), config.contact_active_key_version
        )
        application.state.database = Database(engine)
        application.state.redis = redis
        application.state.sms_provider = build_sms_provider(config)
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
            "available only for explicitly enabled storefronts. Anonymous draft orders expire "
            "with their hold. Customer OTP uses an explicit provider boundary; staging has no "
            "SMS provider, public confirmation and payments. Authenticated customers can read "
            "only their own privacy-minimized order history. Authorized staff order commands "
            "use immutable snapshots and auditable, idempotent state transitions. Staff can "
            "create phone orders with tenant-scoped encrypted contacts; demo tenants accept "
            "only a fixed synthetic contact and do not send SMS. Fulfillment commands cover "
            "handover, return and inspection; contact decryption is separately authorized and "
            "audited. Active customers can request extensions and returns; staff decisions "
            "remain availability-safe. SMS notification jobs fail closed while the provider "
            "is disabled. A privacy-minimized tenant dashboard summarizes daily operations "
            "and inventory without exposing customer contacts or asset identifiers."
            " Assigned outbound deliveries have a separate least-privilege courier queue; "
            "pickup updates the existing order and inventory lifecycle atomically, while "
            "delivery contact access remains explicit and audited."
            " Tenant owners can administer privacy-safe staff memberships and create expiring, "
            "one-time invitations. Staff choose a non-personal login and password at redemption; "
            "they can inventory and revoke their own privacy-minimized active sessions. SMS "
            "delivery, recovery and MFA remain disabled until their production stages."
        ),
    )
    application.add_middleware(ObservabilityMiddleware, metrics=metrics)
    application.include_router(admin_router)
    application.include_router(admin_order_router)
    application.include_router(admin_dashboard_router)
    application.include_router(admin_team_router)
    application.include_router(courier_router)
    application.include_router(staff_auth_router)
    application.include_router(staff_session_router)

    @application.exception_handler(IntegrityError)
    async def integrity_conflict(_request: object, _error: IntegrityError) -> JSONResponse:
        logger.warning("catalog_integrity_conflict")
        return JSONResponse(
            {"detail": "Catalog data conflicts with an existing or related record"},
            status_code=409,
        )

    @application.get("/internal/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        payload, content_type = metrics_response(metrics)
        return Response(payload, media_type=None, headers={"Content-Type": content_type})

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
        "/api/v1/r/{tenant_slug}/checkout/sessions",
        tags=["storefront"],
        response_model=CheckoutSession,
        status_code=201,
    )
    async def public_checkout_session(
        tenant_slug: str,
        request: Request,
        response: Response,
        db: Annotated[Database, Depends(database)],
    ) -> CheckoutSession:
        response.headers["Cache-Control"] = "no-store"
        await enforce_rate_limit(
            request.app.state.redis,
            f"session:{tenant_slug}",
            limit=config.checkout_session_rate_limit,
            window_seconds=config.checkout_rate_limit_window_seconds,
        )
        return await create_checkout_session(
            db,
            tenant_slug,
            allow_demo=config.enable_demo_previews,
            ttl_seconds=config.checkout_session_ttl_seconds,
        )

    @application.post(
        "/api/v1/r/{tenant_slug}/checkout/identity/otp/request",
        tags=["customer authentication"],
        response_model=OtpChallenge,
        status_code=201,
    )
    async def public_customer_otp_request(
        tenant_slug: str,
        command: OtpRequest,
        request: Request,
        response: Response,
        db: Annotated[Database, Depends(database)],
        checkout_token: Annotated[
            str, Header(alias="X-Checkout-Token", min_length=32, max_length=128)
        ],
    ) -> OtpChallenge:
        response.headers["Cache-Control"] = "no-store"
        await enforce_rate_limit(
            request.app.state.redis,
            f"otp-request:{tenant_slug}:{checkout_token}:{command.phone_e164}",
            limit=config.otp_request_rate_limit,
            window_seconds=config.checkout_rate_limit_window_seconds,
        )
        return await request_customer_otp(
            db,
            request.app.state.sms_provider,
            tenant_slug,
            command,
            checkout_token,
            allow_demo=config.enable_demo_previews,
            pepper=config.otp_pepper.get_secret_value(),
            ttl_seconds=config.otp_ttl_seconds,
            max_attempts=config.otp_max_attempts,
        )

    @application.post(
        "/api/v1/r/{tenant_slug}/checkout/identity/otp/verify",
        tags=["customer authentication"],
        response_model=CustomerSession,
    )
    async def public_customer_otp_verify(
        tenant_slug: str,
        command: OtpVerification,
        request: Request,
        response: Response,
        db: Annotated[Database, Depends(database)],
        checkout_token: Annotated[
            str, Header(alias="X-Checkout-Token", min_length=32, max_length=128)
        ],
    ) -> CustomerSession:
        response.headers["Cache-Control"] = "no-store"
        await enforce_rate_limit(
            request.app.state.redis,
            f"otp-verify:{tenant_slug}:{checkout_token}:{command.challenge_id}",
            limit=config.otp_verify_rate_limit,
            window_seconds=config.checkout_rate_limit_window_seconds,
        )
        return await verify_customer_otp(
            db,
            tenant_slug,
            command,
            checkout_token,
            allow_demo=config.enable_demo_previews,
            pepper=config.otp_pepper.get_secret_value(),
            customer_session_ttl_seconds=config.customer_session_ttl_seconds,
        )

    @application.get(
        "/api/v1/r/{tenant_slug}/me",
        tags=["customer authentication"],
        response_model=CustomerIdentity,
    )
    async def public_customer_identity(
        tenant_slug: str,
        response: Response,
        digest: Annotated[bytes, Depends(customer_session_digest)],
        db: Annotated[Database, Depends(database)],
    ) -> CustomerIdentity:
        response.headers["Cache-Control"] = "no-store"
        return await load_customer_identity(
            db,
            tenant_slug,
            digest,
            allow_demo=config.enable_demo_previews,
        )

    @application.get(
        "/api/v1/r/{tenant_slug}/me/orders",
        tags=["customer orders"],
        response_model=CustomerOrderPage,
    )
    async def public_customer_orders(
        tenant_slug: str,
        request: Request,
        response: Response,
        digest: Annotated[bytes, Depends(customer_session_digest)],
        db: Annotated[Database, Depends(database)],
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    ) -> CustomerOrderPage:
        response.headers["Cache-Control"] = "no-store"
        await enforce_rate_limit(
            request.app.state.redis,
            f"customer-read:{tenant_slug}:{digest.hex()}",
            limit=config.checkout_mutation_rate_limit * 3,
            window_seconds=config.checkout_rate_limit_window_seconds,
        )
        return await list_customer_orders(
            db,
            tenant_slug,
            digest,
            allow_demo=config.enable_demo_previews,
            limit=limit,
            offset=offset,
        )

    @application.get(
        "/api/v1/r/{tenant_slug}/me/orders/{order_id}",
        tags=["customer orders"],
        response_model=CustomerOrder,
    )
    async def public_customer_order_detail(
        tenant_slug: str,
        order_id: UUID,
        request: Request,
        response: Response,
        digest: Annotated[bytes, Depends(customer_session_digest)],
        db: Annotated[Database, Depends(database)],
    ) -> CustomerOrder:
        response.headers["Cache-Control"] = "no-store"
        await enforce_rate_limit(
            request.app.state.redis,
            f"customer-read:{tenant_slug}:{digest.hex()}",
            limit=config.checkout_mutation_rate_limit * 3,
            window_seconds=config.checkout_rate_limit_window_seconds,
        )
        return await load_customer_order(
            db,
            tenant_slug,
            order_id,
            digest,
            allow_demo=config.enable_demo_previews,
        )

    @application.post(
        "/api/v1/r/{tenant_slug}/me/orders/{order_id}/extension-requests",
        tags=["customer orders"],
        response_model=ExtensionRequestResult,
        status_code=201,
    )
    async def public_customer_extension_request(
        tenant_slug: str,
        order_id: UUID,
        command: ExtensionRequestCommand,
        request: Request,
        response: Response,
        digest: Annotated[bytes, Depends(customer_session_digest)],
        db: Annotated[Database, Depends(database)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=16, max_length=128)
        ],
    ) -> ExtensionRequestResult:
        response.headers["Cache-Control"] = "no-store"
        await enforce_rate_limit(
            request.app.state.redis,
            f"customer-mutation:{tenant_slug}:{digest.hex()}",
            limit=config.checkout_mutation_rate_limit,
            window_seconds=config.checkout_rate_limit_window_seconds,
        )
        return await create_extension_request(
            db,
            tenant_slug,
            order_id,
            command,
            idempotency_key,
            digest,
            allow_demo=config.enable_demo_previews,
        )

    @application.post(
        "/api/v1/r/{tenant_slug}/me/orders/{order_id}/pickup-requests",
        tags=["customer orders"],
        response_model=ReturnRequestResult,
        status_code=201,
    )
    async def public_customer_return_request(
        tenant_slug: str,
        order_id: UUID,
        command: ReturnRequestCommand,
        request: Request,
        response: Response,
        digest: Annotated[bytes, Depends(customer_session_digest)],
        db: Annotated[Database, Depends(database)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=16, max_length=128)
        ],
    ) -> ReturnRequestResult:
        response.headers["Cache-Control"] = "no-store"
        await enforce_rate_limit(
            request.app.state.redis,
            f"customer-mutation:{tenant_slug}:{digest.hex()}",
            limit=config.checkout_mutation_rate_limit,
            window_seconds=config.checkout_rate_limit_window_seconds,
        )
        return await create_return_request(
            db,
            tenant_slug,
            order_id,
            command,
            idempotency_key,
            digest,
            allow_demo=config.enable_demo_previews,
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
        request: Request,
        response: Response,
        db: Annotated[Database, Depends(database)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=16, max_length=128)
        ],
        checkout_token: Annotated[
            str, Header(alias="X-Checkout-Token", min_length=32, max_length=128)
        ],
    ) -> RentalHold:
        response.headers["Cache-Control"] = "no-store"
        await enforce_rate_limit(
            request.app.state.redis,
            f"mutation:{tenant_slug}:{checkout_token}",
            limit=config.checkout_mutation_rate_limit,
            window_seconds=config.checkout_rate_limit_window_seconds,
        )
        return await create_hold(
            db,
            tenant_slug,
            command,
            idempotency_key,
            checkout_token,
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
        request: Request,
        response: Response,
        db: Annotated[Database, Depends(database)],
        management_token: Annotated[
            str, Header(alias="X-Hold-Token", min_length=32, max_length=128)
        ],
        checkout_token: Annotated[
            str, Header(alias="X-Checkout-Token", min_length=32, max_length=128)
        ],
    ) -> ReleasedHold:
        response.headers["Cache-Control"] = "no-store"
        await enforce_rate_limit(
            request.app.state.redis,
            f"mutation:{tenant_slug}:{checkout_token}",
            limit=config.checkout_mutation_rate_limit,
            window_seconds=config.checkout_rate_limit_window_seconds,
        )
        return await release_hold(
            db,
            tenant_slug,
            hold_id,
            management_token,
            checkout_token,
            allow_demo=config.enable_demo_previews,
        )

    @application.post(
        "/api/v1/r/{tenant_slug}/checkout/orders",
        tags=["storefront"],
        response_model=DraftOrder,
        status_code=201,
    )
    async def public_draft_order(
        tenant_slug: str,
        command: DraftOrderRequest,
        request: Request,
        response: Response,
        db: Annotated[Database, Depends(database)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=16, max_length=128)
        ],
        checkout_token: Annotated[
            str, Header(alias="X-Checkout-Token", min_length=32, max_length=128)
        ],
        hold_token: Annotated[str, Header(alias="X-Hold-Token", min_length=32, max_length=128)],
    ) -> DraftOrder:
        response.headers["Cache-Control"] = "no-store"
        await enforce_rate_limit(
            request.app.state.redis,
            f"mutation:{tenant_slug}:{checkout_token}",
            limit=config.checkout_mutation_rate_limit,
            window_seconds=config.checkout_rate_limit_window_seconds,
        )
        return await create_draft_order(
            db,
            tenant_slug,
            command,
            idempotency_key,
            checkout_token,
            hold_token,
            allow_demo=config.enable_demo_previews,
        )

    @application.get(
        "/api/v1/r/{tenant_slug}/checkout/orders/{order_id}",
        tags=["storefront"],
        response_model=DraftOrder,
    )
    async def public_draft_order_detail(
        tenant_slug: str,
        order_id: UUID,
        request: Request,
        response: Response,
        db: Annotated[Database, Depends(database)],
        checkout_token: Annotated[
            str, Header(alias="X-Checkout-Token", min_length=32, max_length=128)
        ],
    ) -> DraftOrder:
        response.headers["Cache-Control"] = "no-store"
        await enforce_rate_limit(
            request.app.state.redis,
            f"read:{tenant_slug}:{checkout_token}",
            limit=config.checkout_mutation_rate_limit * 3,
            window_seconds=config.checkout_rate_limit_window_seconds,
        )
        return await load_draft_order(
            db,
            tenant_slug,
            order_id,
            checkout_token,
            allow_demo=config.enable_demo_previews,
        )

    @application.post(
        "/api/v1/auth/demo-session",
        tags=["authentication"],
        response_model=DemoSession,
    )
    async def demo_session(
        request: Request,
        response: Response,
        db: Annotated[Database, Depends(database)],
    ) -> DemoSession:
        if not config.enable_demo_previews or config.app_env == "production":
            raise HTTPException(status_code=404, detail="Demo session unavailable")
        response.headers["Cache-Control"] = "no-store"
        forwarded_user_agent = request.headers.get("x-photolamp-client-user-agent")
        return await issue_demo_session(
            db,
            client_label=coarse_client_label(
                forwarded_user_agent or request.headers.get("user-agent")
            ),
        )

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
