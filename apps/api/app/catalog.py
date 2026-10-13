"""Public, privacy-safe catalog read model backed by tenant-scoped tables."""

from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.database import Database, PublicStorefrontNotFound


class CatalogBranch(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    city: str
    timezone: str
    pickup_enabled: bool
    delivery_enabled: bool


class CatalogItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    category: Literal["phototherapy_lamp"]
    manufacturer: str | None
    description: str
    included_items: list[str]
    illustration: Literal["floor", "compact", "mobile"]
    amount_minor_per_day: int = Field(gt=0)
    deposit_minor: int = Field(ge=0)
    currency: Literal["RUB"]
    minimum_days: int = Field(ge=1)
    maximum_days: int = Field(ge=1)
    price_is_demo: bool
    listed_units: int = Field(ge=0)
    serviceable_units: int = Field(ge=0)


class PublicCatalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    display_name: str
    city: str
    timezone: str
    accent: Literal["sun", "mint"]
    public_note: str
    is_demo: bool
    booking_enabled: bool
    availability_confirmed: Literal[False] = False
    phone: None = None
    branches: list[CatalogBranch]
    items: list[CatalogItem]


class PreviewTenant(BaseModel):
    """Compatibility DTO for the initial storefront preview route."""

    model_config = ConfigDict(frozen=True)

    slug: str
    display_name: str
    city: str
    timezone: str
    accent: Literal["sun", "mint"]
    is_demo: bool
    booking_enabled: bool
    phone: None = None
    tariff: None = None


async def load_public_catalog(database: Database, slug: str, *, allow_demo: bool) -> PublicCatalog:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            storefront = (
                await transaction.session.execute(
                    text(
                        "SELECT t.slug, t.display_name, t.timezone, s.city, s.accent, "
                        "s.public_note, s.is_demo, s.booking_enabled "
                        "FROM tenants t JOIN storefront_profiles s ON s.tenant_id = t.id "
                        "WHERE t.id = :tenant_id"
                    ),
                    {"tenant_id": transaction.tenant_id},
                )
            ).one()
            branch_rows = (
                await transaction.session.execute(
                    text(
                        "SELECT id, name, city, timezone, pickup_enabled, delivery_enabled "
                        "FROM branches WHERE tenant_id = :tenant_id AND state = 'active' "
                        "ORDER BY name"
                    ),
                    {"tenant_id": transaction.tenant_id},
                )
            ).all()
            item_rows = (
                await transaction.session.execute(
                    text(
                        "SELECT m.id, m.name, m.category, m.manufacturer, m.description, "
                        "m.included_items, m.illustration, p.amount_minor_per_day, "
                        "p.deposit_minor, p.currency, "
                        "p.minimum_days, p.maximum_days, p.is_demo AS price_is_demo, "
                        "count(a.id) FILTER (WHERE a.state <> 'retired')::integer "
                        "AS listed_units, "
                        "count(a.id) FILTER (WHERE a.state = 'available')::integer "
                        "AS serviceable_units "
                        "FROM equipment_models m "
                        "JOIN pricing_rules p ON p.tenant_id = m.tenant_id "
                        "AND p.equipment_model_id = m.id AND p.state = 'active' "
                        "LEFT JOIN assets a ON a.tenant_id = m.tenant_id "
                        "AND a.equipment_model_id = m.id "
                        "WHERE m.tenant_id = :tenant_id "
                        "AND m.publication_state = 'published' "
                        "GROUP BY m.id, m.name, m.category, m.manufacturer, m.description, "
                        "m.included_items, m.illustration, p.amount_minor_per_day, "
                        "p.deposit_minor, p.currency, "
                        "p.minimum_days, p.maximum_days, p.is_demo "
                        "ORDER BY m.name"
                    ),
                    {"tenant_id": transaction.tenant_id},
                )
            ).all()
            return PublicCatalog(
                **dict(storefront._mapping),
                branches=[CatalogBranch.model_validate(row._mapping) for row in branch_rows],
                items=[CatalogItem.model_validate(row._mapping) for row in item_rows],
            )
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None


def preview_from_catalog(catalog: PublicCatalog) -> PreviewTenant:
    return PreviewTenant(
        slug=catalog.slug,
        display_name=catalog.display_name,
        city=catalog.city,
        timezone=catalog.timezone,
        accent=catalog.accent,
        is_demo=catalog.is_demo,
        booking_enabled=catalog.booking_enabled,
    )
