"""Public date-aware availability and server-side quote calculation."""

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.database import (
    Database,
    PublicStorefrontNotFound,
    PublicTenantTransaction,
    TenantTransaction,
)

Fulfillment = Literal["pickup", "delivery"]


def _as_int(value: object) -> int:
    if not isinstance(value, int):
        raise RuntimeError("Database returned an invalid numeric quote field")
    return value


def _as_date(value: object) -> date:
    if not isinstance(value, date):
        raise RuntimeError("Database returned an invalid quote date")
    return value


class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: UUID
    branch_id: UUID
    start_date: date
    end_date: date
    fulfillment: Fulfillment


class Availability(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_id: UUID
    branch_id: UUID
    start_date: date
    end_date: date
    days: int = Field(ge=1, le=365)
    available_units: int = Field(ge=0)
    available: bool
    availability_confirmed: Literal[True] = True
    reservation_created: Literal[False] = False


class Quote(Availability):
    fulfillment: Fulfillment
    amount_minor_per_day: int = Field(gt=0)
    rental_amount_minor: int = Field(gt=0)
    deposit_minor: int = Field(ge=0)
    delivery_amount_minor: Literal[0] = 0
    total_minor: int = Field(gt=0)
    currency: Literal["RUB"]
    price_is_demo: bool
    booking_enabled: Literal[False] = False


def _rental_days(start_date: date, end_date: date) -> int:
    days = (end_date - start_date).days + 1
    if days < 1:
        raise HTTPException(status_code=422, detail="end_date must not be before start_date")
    if days > 365:
        raise HTTPException(status_code=422, detail="Rental period cannot exceed 365 days")
    return days


async def load_quote_context(
    transaction: PublicTenantTransaction | TenantTransaction, request: QuoteRequest
) -> tuple[dict[str, object], int]:
    days = _rental_days(request.start_date, request.end_date)
    row = (
        await transaction.session.execute(
            text(
                "SELECT m.id AS model_id, b.id AS branch_id, "
                "b.pickup_enabled, b.delivery_enabled, s.booking_enabled, "
                "(now() AT TIME ZONE b.timezone)::date AS local_date, "
                "p.amount_minor_per_day, p.deposit_minor, p.currency, "
                "p.minimum_days, p.maximum_days, p.is_demo AS price_is_demo, "
                "count(a.id) FILTER (WHERE a.id IS NOT NULL AND NOT EXISTS ("
                "  SELECT 1 FROM asset_blocks ab WHERE ab.tenant_id = a.tenant_id "
                "  AND ab.asset_id = a.id AND ab.start_date <= :end_date "
                "  AND ab.end_date >= :start_date"
                ") AND NOT EXISTS ("
                "  SELECT 1 FROM rental_allocations ra WHERE ra.tenant_id = a.tenant_id "
                "  AND ra.asset_id = a.id AND ra.start_date <= :end_date "
                "  AND ra.end_date >= :start_date AND (ra.state = 'confirmed' "
                "    OR (ra.state = 'held' AND ra.expires_at > now()))"
                "))::integer AS available_units "
                "FROM equipment_models m "
                "JOIN storefront_profiles s ON s.tenant_id = m.tenant_id "
                "JOIN branches b ON b.tenant_id = m.tenant_id AND b.id = :branch_id "
                "  AND b.state = 'active' "
                "JOIN pricing_rules p ON p.tenant_id = m.tenant_id "
                "  AND p.equipment_model_id = m.id AND p.state = 'active' "
                "LEFT JOIN assets a ON a.tenant_id = m.tenant_id "
                "  AND a.equipment_model_id = m.id AND a.branch_id = b.id "
                "  AND a.state = 'available' "
                "WHERE m.tenant_id = :tenant_id AND m.id = :model_id "
                "  AND m.publication_state = 'published' "
                "GROUP BY m.id, b.id, b.timezone, b.pickup_enabled, b.delivery_enabled, "
                "s.booking_enabled, p.amount_minor_per_day, p.deposit_minor, p.currency, "
                "p.minimum_days, p.maximum_days, p.is_demo"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "model_id": request.model_id,
                "branch_id": request.branch_id,
                "start_date": request.start_date,
                "end_date": request.end_date,
            },
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Published model or branch not found")
    context = dict(row._mapping)
    if request.start_date < _as_date(context["local_date"]):
        raise HTTPException(status_code=422, detail="start_date must not be in the past")
    return context, days


async def _load_quote_context(
    database: Database,
    slug: str,
    request: QuoteRequest,
    *,
    allow_demo: bool,
) -> tuple[dict[str, object], int]:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            return await load_quote_context(transaction, request)
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None


async def check_availability(
    database: Database,
    slug: str,
    request: QuoteRequest,
    *,
    allow_demo: bool,
) -> Availability:
    context, days = await _load_quote_context(database, slug, request, allow_demo=allow_demo)
    available_units = _as_int(context["available_units"])
    return Availability(
        model_id=request.model_id,
        branch_id=request.branch_id,
        start_date=request.start_date,
        end_date=request.end_date,
        days=days,
        available_units=available_units,
        available=available_units > 0,
    )


async def calculate_quote(
    database: Database,
    slug: str,
    request: QuoteRequest,
    *,
    allow_demo: bool,
) -> Quote:
    context, days = await _load_quote_context(database, slug, request, allow_demo=allow_demo)
    return quote_from_context(request, context, days)


def quote_from_context(request: QuoteRequest, context: dict[str, object], days: int) -> Quote:
    minimum_days = _as_int(context["minimum_days"])
    maximum_days = _as_int(context["maximum_days"])
    if not minimum_days <= days <= maximum_days:
        raise HTTPException(
            status_code=422,
            detail=f"Rental period must be between {minimum_days} and {maximum_days} days",
        )
    if not bool(context[f"{request.fulfillment}_enabled"]):
        raise HTTPException(status_code=422, detail="Selected fulfillment is unavailable")
    available_units = _as_int(context["available_units"])
    if available_units == 0:
        raise HTTPException(status_code=409, detail="No units available for selected dates")
    daily = _as_int(context["amount_minor_per_day"])
    deposit = _as_int(context["deposit_minor"])
    rental = daily * days
    return Quote(
        model_id=request.model_id,
        branch_id=request.branch_id,
        start_date=request.start_date,
        end_date=request.end_date,
        days=days,
        available_units=available_units,
        available=True,
        fulfillment=request.fulfillment,
        amount_minor_per_day=daily,
        rental_amount_minor=rental,
        deposit_minor=deposit,
        total_minor=rental + deposit,
        currency="RUB",
        price_is_demo=bool(context["price_is_demo"]),
    )
