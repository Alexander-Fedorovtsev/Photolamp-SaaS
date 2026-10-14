"""HTTP boundary for tenant staff order operations."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from app.admin_orders import (
    ContactAccessResult,
    ExtensionDecisionResult,
    InspectionRequest,
    OrderContactUnavailable,
    OrderNotFound,
    OrderPermissionDenied,
    OrderState,
    OrderTransitionConflict,
    OrderTransitionResult,
    RequestDecision,
    ReturnDecisionResult,
    ReturnRequestDecision,
    StaffOrder,
    StaffOrderPage,
    access_order_contact,
    cancel_order,
    complete_order_inspection,
    confirm_order,
    decide_extension_request,
    decide_return_request,
    hand_over_order,
    list_staff_orders,
    load_staff_order,
    return_order,
)
from app.database import TenantTransaction
from app.phone_orders import PhoneOrderRequest, create_phone_order
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
    if isinstance(error, OrderContactUnavailable):
        return HTTPException(status_code=409, detail="Order contact details are unavailable")
    raise error


@router.get("", response_model=StaffOrderPage)
async def staff_orders(
    response: Response,
    transaction: Transaction,
    state: Annotated[OrderState | None, Query()] = None,
    overdue: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> StaffOrderPage:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await list_staff_orders(
            transaction, state=state, overdue=overdue, limit=limit, offset=offset
        )
    except OrderPermissionDenied as error:
        raise translate_order_error(error) from None


@router.post("/phone", response_model=OrderTransitionResult, status_code=201)
async def staff_phone_order(
    command: PhoneOrderRequest,
    request: Request,
    response: Response,
    transaction: Transaction,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> OrderTransitionResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await create_phone_order(
            transaction,
            command,
            idempotency_key,
            pepper=request.app.state.config.otp_pepper.get_secret_value(),
            cipher=request.app.state.contact_cipher,
        )
    except (OrderPermissionDenied, OrderTransitionConflict) as error:
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


@router.post("/{order_id}/contact-access", response_model=ContactAccessResult)
async def staff_access_order_contact(
    order_id: UUID,
    request: Request,
    response: Response,
    transaction: Transaction,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> ContactAccessResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await access_order_contact(
            transaction,
            order_id,
            idempotency_key,
            request.app.state.contact_cipher,
        )
    except (
        OrderPermissionDenied,
        OrderNotFound,
        OrderTransitionConflict,
        OrderContactUnavailable,
    ) as error:
        raise translate_order_error(error) from None


@router.post("/{order_id}/handover", response_model=OrderTransitionResult)
async def staff_hand_over_order(
    order_id: UUID,
    response: Response,
    transaction: Transaction,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> OrderTransitionResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await hand_over_order(transaction, order_id, idempotency_key)
    except (OrderPermissionDenied, OrderNotFound, OrderTransitionConflict) as error:
        raise translate_order_error(error) from None


@router.post("/{order_id}/return", response_model=OrderTransitionResult)
async def staff_return_order(
    order_id: UUID,
    response: Response,
    transaction: Transaction,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> OrderTransitionResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await return_order(transaction, order_id, idempotency_key)
    except (OrderPermissionDenied, OrderNotFound, OrderTransitionConflict) as error:
        raise translate_order_error(error) from None


@router.post("/{order_id}/inspection", response_model=OrderTransitionResult)
async def staff_complete_order_inspection(
    command: InspectionRequest,
    order_id: UUID,
    response: Response,
    transaction: Transaction,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> OrderTransitionResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await complete_order_inspection(
            transaction, order_id, idempotency_key, command.outcome
        )
    except (OrderPermissionDenied, OrderNotFound, OrderTransitionConflict) as error:
        raise translate_order_error(error) from None


@router.post(
    "/{order_id}/extension-requests/{request_id}/approve",
    response_model=ExtensionDecisionResult,
)
async def staff_approve_extension_request(
    order_id: UUID,
    request_id: UUID,
    response: Response,
    transaction: Transaction,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> ExtensionDecisionResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await decide_extension_request(
            transaction, order_id, request_id, idempotency_key, approve=True
        )
    except (OrderPermissionDenied, OrderNotFound, OrderTransitionConflict) as error:
        raise translate_order_error(error) from None


@router.post(
    "/{order_id}/extension-requests/{request_id}/reject",
    response_model=ExtensionDecisionResult,
)
async def staff_reject_extension_request(
    command: RequestDecision,
    order_id: UUID,
    request_id: UUID,
    response: Response,
    transaction: Transaction,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> ExtensionDecisionResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await decide_extension_request(
            transaction,
            order_id,
            request_id,
            idempotency_key,
            approve=False,
            rejection_reason=command.reason,
        )
    except (OrderPermissionDenied, OrderNotFound, OrderTransitionConflict) as error:
        raise translate_order_error(error) from None


@router.post(
    "/{order_id}/pickup-requests/{request_id}/acknowledge",
    response_model=ReturnDecisionResult,
)
async def staff_acknowledge_return_request(
    order_id: UUID,
    request_id: UUID,
    response: Response,
    transaction: Transaction,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> ReturnDecisionResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await decide_return_request(
            transaction, order_id, request_id, idempotency_key, acknowledge=True
        )
    except (OrderPermissionDenied, OrderNotFound, OrderTransitionConflict) as error:
        raise translate_order_error(error) from None


@router.post(
    "/{order_id}/pickup-requests/{request_id}/reject",
    response_model=ReturnDecisionResult,
)
async def staff_reject_return_request(
    command: ReturnRequestDecision,
    order_id: UUID,
    request_id: UUID,
    response: Response,
    transaction: Transaction,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> ReturnDecisionResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await decide_return_request(
            transaction,
            order_id,
            request_id,
            idempotency_key,
            acknowledge=False,
            rejection_reason=command.reason,
        )
    except (OrderPermissionDenied, OrderNotFound, OrderTransitionConflict) as error:
        raise translate_order_error(error) from None
