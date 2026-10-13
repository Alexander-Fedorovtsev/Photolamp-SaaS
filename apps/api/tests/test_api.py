from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.catalog import CatalogBranch, CatalogItem, PublicCatalog
from app.config import Settings
from app.main import create_app


def catalog(slug: str) -> PublicCatalog:
    city, display_name, accent = (
        ("Уфа", "Солнечный дом · демо", "sun")
        if slug == "demo-ufa"
        else ("Казань", "Бережный свет · демо", "mint")
    )
    return PublicCatalog(
        slug=slug,
        display_name=display_name,
        city=city,
        timezone="Asia/Yekaterinburg" if slug == "demo-ufa" else "Europe/Moscow",
        accent=accent,
        public_note="Synthetic catalog",
        is_demo=True,
        booking_enabled=False,
        branches=[
            CatalogBranch(
                id="d1000000-0000-0000-0000-000000000001",
                name="Demo branch",
                city=city,
                timezone="Asia/Yekaterinburg",
                pickup_enabled=True,
                delivery_enabled=True,
            )
        ],
        items=[
            CatalogItem(
                id="d2000000-0000-0000-0000-000000000001",
                name="Demo lamp",
                category="phototherapy_lamp",
                manufacturer=None,
                description="Synthetic item",
                included_items=["lamp", "case", "instructions"],
                illustration="floor",
                amount_minor_per_day=49000,
                deposit_minor=0,
                currency="RUB",
                minimum_days=3,
                maximum_days=30,
                price_is_demo=True,
                listed_units=2,
                serviceable_units=1,
            )
        ],
    )


@pytest.fixture(autouse=True)
def catalog_loader() -> Iterator[AsyncMock]:
    async def load(_: object, slug: str, *, allow_demo: bool) -> PublicCatalog:
        if allow_demo and slug in {"demo-ufa", "demo-kazan"}:
            return catalog(slug)
        raise HTTPException(status_code=404, detail="Storefront not found")

    loader = AsyncMock(side_effect=load)
    with patch("app.main.load_public_catalog", new=loader):
        yield loader


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(Settings(app_env="test", enable_demo_previews=True))) as client:
        yield client


def test_liveness_does_not_require_dependencies(client: TestClient) -> None:
    assert client.get("/api/v1/health/live").json() == {"status": "alive"}


def test_readiness_success(client: TestClient) -> None:
    with (
        patch("app.health.check_database", new=AsyncMock()),
        patch(
            "redis.asyncio.Redis.ping",
            new=AsyncMock(return_value=True),
        ),
    ):
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("failed", ["database", "redis", "both"])
def test_dependency_failures_are_safe(client: TestClient, failed: str) -> None:
    secret_error = RuntimeError("password=do-not-expose")
    database = AsyncMock(side_effect=secret_error if failed in {"database", "both"} else None)
    redis = AsyncMock(
        return_value=True, side_effect=secret_error if failed in {"redis", "both"} else None
    )
    with (
        patch("app.health.check_database", new=database),
        patch("redis.asyncio.Redis.ping", new=redis),
    ):
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert "password" not in response.text


def test_distinct_public_previews(client: TestClient) -> None:
    ufa = client.get("/api/v1/r/demo-ufa/preview").json()
    kazan = client.get("/api/v1/r/demo-kazan/preview").json()
    assert ufa["slug"] != kazan["slug"]
    assert ufa["city"] == "Уфа"
    assert kazan["city"] == "Казань"
    for tenant in [ufa, kazan]:
        assert tenant["is_demo"] is True
        assert tenant["booking_enabled"] is False
        assert tenant["phone"] is None
        assert tenant["tariff"] is None


def test_public_catalog_is_explicit_demo_data(client: TestClient) -> None:
    response = client.get("/api/v1/r/demo-ufa/catalog")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["availability_confirmed"] is False
    assert body["booking_enabled"] is False
    assert body["items"][0]["price_is_demo"] is True
    assert body["items"][0]["amount_minor_per_day"] == 49000


def test_headers_do_not_change_public_slug(client: TestClient) -> None:
    response = client.get("/api/v1/r/demo-ufa/preview", headers={"X-Tenant-ID": "demo-kazan"})
    assert response.json()["slug"] == "demo-ufa"


def test_unknown_tenant_does_not_fall_back(client: TestClient) -> None:
    assert client.get("/api/v1/r/unknown/preview").status_code == 404


def test_previews_disabled_by_default() -> None:
    with TestClient(create_app(Settings(app_env="test", enable_demo_previews=False))) as client:
        assert client.get("/api/v1/r/demo-ufa/preview").status_code == 404


def test_no_booking_or_auth_endpoints(client: TestClient) -> None:
    for path in [
        "/api/v1/auth/otp/request",
        "/api/v1/r/demo-ufa/orders",
        "/api/v1/webhooks/billing",
    ]:
        assert client.post(path, json={}).status_code == 404


def test_openapi_matches_catalog_contract(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/v1/r/{tenant_slug}/catalog" in schema["paths"]
    assert "/api/v1/r/{tenant_slug}/availability" in schema["paths"]
    assert "/api/v1/r/{tenant_slug}/checkout/quote" in schema["paths"]
    assert "/api/v1/r/{tenant_slug}/checkout/sessions" in schema["paths"]
    assert "/api/v1/r/{tenant_slug}/checkout/holds" in schema["paths"]
    assert "/api/v1/r/{tenant_slug}/checkout/orders" in schema["paths"]
    draft = schema["components"]["schemas"]["DraftOrder"]["properties"]
    assert draft["identity_verified"]["type"] == "boolean"
    assert draft["confirmation_available"]["const"] is False
    assert draft["payment_status"]["const"] == "not_started"
    assert "/api/v1/r/{tenant_slug}/checkout/identity/otp/request" in schema["paths"]
    assert "/api/v1/r/{tenant_slug}/checkout/identity/otp/verify" in schema["paths"]
    assert "/api/v1/r/{tenant_slug}/me/orders" in schema["paths"]
    assert "/api/v1/r/{tenant_slug}/me/orders/{order_id}" in schema["paths"]
    customer_order = schema["components"]["schemas"]["CustomerOrder"]["properties"]
    assert customer_order["identity_verified"]["const"] is True
    assert customer_order["confirmation_available"]["const"] is False
    assert customer_order["payment_status"]["const"] == "not_started"
    assert (
        schema["components"]["schemas"]["CustomerSession"]["properties"]["identity_verified"][
            "const"
        ]
        is True
    )
    assert (
        schema["components"]["schemas"]["PublicCatalog"]["properties"]["availability_confirmed"][
            "const"
        ]
        is False
    )
    assert "/api/v1/health/ready" in schema["paths"]
    assert "/api/v1/me/tenants" in schema["paths"]
    assert "/api/v1/t/{tenant_id}/context" in schema["paths"]
    assert "HTTPBearer" in schema["components"]["securitySchemes"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"app_env": "production", "enable_demo_previews": True},
        {"app_env": "production", "enable_demo_previews": False},
        {"database_url": "sqlite:///:memory:"},
    ],
)
def test_unsafe_settings_fail(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_settings_repr_redacts_credentials() -> None:
    assert "local-development-only" not in repr(Settings())


def test_staging_accepts_only_explicit_versioned_demo_configuration() -> None:
    settings = Settings(
        app_env="staging",
        enable_demo_previews=True,
        database_url="postgresql+asyncpg://photolamp:synthetic-test-password@localhost/photolamp",
        photolamp_deploy_sha="a" * 40,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/r/demo-ufa/preview").status_code == 200
        response = client.get("/api/v1/version")
        assert response.json() == {"git_sha": "a" * 40}
        assert response.headers["cache-control"] == "no-store"
    with pytest.raises(ValidationError, match="exact Git SHA"):
        Settings(
            app_env="staging",
            enable_demo_previews=True,
            database_url="postgresql+asyncpg://photolamp:synthetic-test-password@localhost/photolamp",
        )


def test_versioned_production_still_rejects_demo_previews() -> None:
    with pytest.raises(ValidationError, match="Demo previews must be disabled"):
        Settings(
            app_env="production",
            enable_demo_previews=True,
            database_url="postgresql+asyncpg://photolamp:synthetic-test-password@localhost/photolamp",
            photolamp_deploy_sha="a" * 40,
        )


def test_memory_sms_provider_is_rejected_outside_local_environments() -> None:
    with pytest.raises(ValidationError, match="memory SMS provider"):
        Settings(
            app_env="staging",
            enable_demo_previews=True,
            sms_provider_mode="memory",
            database_url=(
                "postgresql+asyncpg://photolamp:synthetic-test-password@localhost/photolamp"
            ),
            photolamp_deploy_sha="a" * 40,
        )
