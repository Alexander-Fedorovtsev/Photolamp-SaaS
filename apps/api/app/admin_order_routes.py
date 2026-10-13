"""HTTP boundary for tenant staff order operations."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.admin_orders import (
    OrderNotFound,
    OrderPermissionDenied,
    OrderState,
    OrderTransitionConflict,
    OrderTransitionResult,
    StaffOrder,
    StaffOrderPage,
    cancel_order,
    confirm_order,
    list_staff_orders,
    load_staff_order,
)
from app.database import TenantTransaction
from app.tenancy import authorized_tenant_transaction

router = APIRouter(prefix="/api/v1/t/{tenant_id}/orders", tags=["tenant orders"])
Transaction = Annotated[TenantTransaction, Depends(authorized_tenant_transaction)]


def translate_order_error(error: Exception) -> HTTPException:
    if isinstance(error, OrderPermissionDenied):
        return HTTPException(status_code=403, detail="Owner, admin or operator role required")
    if isinstance(error, OrderNotFound):
        return HTTPException(status_code=404, detail="Order not found")
    if isinstance(error, OrderTransitionConflict):
        return HTTPException(status_code=409, detail=str(error))
    raise error


@router.get("", response_model=StaffOrderPage)
async def staff_orders(
    response: Response,
    transaction: Transaction,
    state: Annotated[OrderState | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> StaffOrderPage:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await list_staff_orders(transaction, state=state, limit=limit, offset=offset)
    except OrderPermissionDenied as error:
        raise translate_order_error(error) from None


@router.get("/{order_id}", response_model=StaffOrder)
async def staff_order(order_id: UUID, response: Response, transaction: Transaction) -> StaffOrder:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await load_staff_order(transaction, order_id)
    except (OrderPermissionDenied, OrderNotFound) as error:
        raise translate_order_error(error) from None


@router.post("/{order_id}/confirm", response_model=OrderTransitionResult)
async def staff_confirm_order(
    order_id: UUID,
    response: Response,
    transaction: Transaction,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> OrderTransitionResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await confirm_order(transaction, order_id, idempotency_key)
    except (OrderPermissionDenied, OrderNotFound, OrderTransitionConflict) as error:
        raise translate_order_error(error) from None


@router.post("/{order_id}/cancel", response_model=OrderTransitionResult)
async def staff_cancel_order(
    order_id: UUID,
    response: Response,
    transaction: Transaction,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> OrderTransitionResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await cancel_order(transaction, order_id, idempotency_key)
    except (OrderPermissionDenied, OrderNotFound, OrderTransitionConflict) as error:
        raise translate_order_error(error) from None
