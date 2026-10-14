"""Privacy-minimized operational dashboard for authorized tenant staff."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.admin_orders import OrderSource, OrderState, require_order_operator
from app.database import TenantTransaction
from app.holds import expire_holds


class DashboardCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    awaiting_confirmation: int = Field(ge=0)
    handovers_due: int = Field(ge=0)
    returns_today: int = Field(ge=0)
    returns_tomorrow: int = Field(ge=0)
    overdue: int = Field(ge=0)
    inspection: int = Field(ge=0)
    pending_requests: int = Field(ge=0)


class InventoryCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    available: int = Field(ge=0)
    rented: int = Field(ge=0)
    inspection: int = Field(ge=0)
    maintenance: int = Field(ge=0)
    retired: int = Field(ge=0)


class DashboardOrder(BaseModel):
    """Enough order context to triage work, without customer or asset details."""

    model_config = ConfigDict(frozen=True)

    order_id: UUID
    state: OrderState
    source: OrderSource
    model_name: str
    branch_name: str
    start_date: date
    end_date: date
    fulfillment: Literal["pickup", "delivery"]
    overdue_days: int = Field(ge=0)
    pending_request_types: list[Literal["extension", "return"]]


class OperationsDashboard(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    timezone: str
    local_date: date
    generated_at: datetime
    counts: DashboardCounts
    inventory: InventoryCounts
    handovers_due: list[DashboardOrder]
    returns_today: list[DashboardOrder]
    returns_tomorrow: list[DashboardOrder]
    overdue: list[DashboardOrder]
    pending_requests: list[DashboardOrder]


DASHBOARD_ORDER_PROJECTION = """
SELECT o.id, o.state, o.source, o.start_date, o.end_date, o.fulfillment,
  o.order_snapshot->>'model_name' AS model_name,
  o.order_snapshot->>'branch_name' AS branch_name,
  GREATEST(((now() AT TIME ZONE b.timezone)::date - o.end_date), 0)::integer AS overdue_days,
  EXISTS (
    SELECT 1 FROM order_extension_requests er
    WHERE er.tenant_id = o.tenant_id AND er.order_id = o.id AND er.state = 'pending'
  ) AS extension_pending,
  EXISTS (
    SELECT 1 FROM order_return_requests rr
    WHERE rr.tenant_id = o.tenant_id AND rr.order_id = o.id AND rr.state = 'pending'
  ) AS return_pending
FROM orders o
JOIN branches b ON b.tenant_id = o.tenant_id AND b.id = o.branch_id
"""


def _dashboard_order(row: object) -> DashboardOrder:
    mapping = row._mapping  # type: ignore[attr-defined]
    pending: list[Literal["extension", "return"]] = []
    if mapping["extension_pending"]:
        pending.append("extension")
    if mapping["return_pending"]:
        pending.append("return")
    return DashboardOrder(
        order_id=mapping["id"],
        state=mapping["state"],
        source=mapping["source"],
        model_name=mapping["model_name"],
        branch_name=mapping["branch_name"],
        start_date=mapping["start_date"],
        end_date=mapping["end_date"],
        fulfillment=mapping["fulfillment"],
        overdue_days=mapping["overdue_days"] if mapping["state"] == "in_progress" else 0,
        pending_request_types=pending,
    )


async def _load_dashboard_orders(
    transaction: TenantTransaction, predicate: str, order_by: str
) -> list[DashboardOrder]:
    rows: Sequence[object] = (
        await transaction.session.execute(
            text(
                DASHBOARD_ORDER_PROJECTION
                + " WHERE o.tenant_id = :tenant_id AND ("
                + predicate
                + ") ORDER BY "
                + order_by
                + " LIMIT 8"
            ),
            {"tenant_id": transaction.tenant_id},
        )
    ).all()
    return [_dashboard_order(row) for row in rows]


async def load_operations_dashboard(transaction: TenantTransaction) -> OperationsDashboard:
    require_order_operator(transaction)
    await expire_holds(transaction)

    context = (
        await transaction.session.execute(
            text(
                "SELECT timezone, (now() AT TIME ZONE timezone)::date AS local_date, "
                "now() AS generated_at FROM tenants WHERE id = :tenant_id"
            ),
            {"tenant_id": transaction.tenant_id},
        )
    ).one()
    counts = (
        await transaction.session.execute(
            text(
                "SELECT "
                "count(*) FILTER (WHERE o.state = 'held')::integer AS awaiting_confirmation, "
                "count(*) FILTER (WHERE o.state = 'confirmed' AND o.start_date <= "
                "(now() AT TIME ZONE b.timezone)::date)::integer AS handovers_due, "
                "count(*) FILTER (WHERE o.state = 'in_progress' AND o.end_date = "
                "(now() AT TIME ZONE b.timezone)::date)::integer AS returns_today, "
                "count(*) FILTER (WHERE o.state = 'in_progress' AND o.end_date = "
                "(now() AT TIME ZONE b.timezone)::date + 1)::integer AS returns_tomorrow, "
                "count(*) FILTER (WHERE o.state = 'in_progress' AND o.end_date < "
                "(now() AT TIME ZONE b.timezone)::date)::integer AS overdue, "
                "count(*) FILTER (WHERE o.state = 'returned')::integer AS inspection, "
                "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM order_extension_requests er "
                "WHERE er.tenant_id = o.tenant_id AND er.order_id = o.id AND er.state = 'pending') "
                "OR EXISTS (SELECT 1 FROM order_return_requests rr WHERE "
                "rr.tenant_id = o.tenant_id "
                "AND rr.order_id = o.id AND rr.state = 'pending'))::integer AS pending_requests "
                "FROM orders o JOIN branches b ON b.tenant_id = o.tenant_id AND b.id = o.branch_id "
                "WHERE o.tenant_id = :tenant_id"
            ),
            {"tenant_id": transaction.tenant_id},
        )
    ).one()
    inventory_rows = (
        await transaction.session.execute(
            text(
                "SELECT state, count(*)::integer AS total FROM assets "
                "WHERE tenant_id = :tenant_id GROUP BY state"
            ),
            {"tenant_id": transaction.tenant_id},
        )
    ).all()
    inventory_values = {row.state: row.total for row in inventory_rows}

    handovers_due = await _load_dashboard_orders(
        transaction,
        "o.state = 'confirmed' AND o.start_date <= (now() AT TIME ZONE b.timezone)::date",
        "o.start_date, o.created_at, o.id",
    )
    returns_today = await _load_dashboard_orders(
        transaction,
        "o.state = 'in_progress' AND o.end_date = (now() AT TIME ZONE b.timezone)::date",
        "o.end_date, o.created_at, o.id",
    )
    returns_tomorrow = await _load_dashboard_orders(
        transaction,
        "o.state = 'in_progress' AND o.end_date = (now() AT TIME ZONE b.timezone)::date + 1",
        "o.end_date, o.created_at, o.id",
    )
    overdue = await _load_dashboard_orders(
        transaction,
        "o.state = 'in_progress' AND o.end_date < (now() AT TIME ZONE b.timezone)::date",
        "o.end_date, o.created_at, o.id",
    )
    pending_requests = await _load_dashboard_orders(
        transaction,
        "EXISTS (SELECT 1 FROM order_extension_requests er WHERE er.tenant_id = o.tenant_id "
        "AND er.order_id = o.id AND er.state = 'pending') OR EXISTS (SELECT 1 FROM "
        "order_return_requests rr WHERE rr.tenant_id = o.tenant_id AND rr.order_id = o.id "
        "AND rr.state = 'pending')",
        "o.updated_at DESC, o.id DESC",
    )

    count_values = dict(counts._mapping)
    return OperationsDashboard(
        tenant_id=transaction.tenant_id,
        timezone=context.timezone,
        local_date=context.local_date,
        generated_at=context.generated_at,
        counts=DashboardCounts(**count_values),
        inventory=InventoryCounts(
            **{
                state: int(inventory_values.get(state, 0))
                for state in ("available", "rented", "inspection", "maintenance", "retired")
            }
        ),
        handovers_due=handovers_due,
        returns_today=returns_today,
        returns_tomorrow=returns_tomorrow,
        overdue=overdue,
        pending_requests=pending_requests,
    )
