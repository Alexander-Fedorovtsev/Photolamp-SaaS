"""HTTP boundary for courier dispatch and assigned delivery tasks."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from app.courier import (
    CourierAssignmentCommand,
    CourierAssignmentResult,
    CourierConflict,
    CourierContactResult,
    CourierContactUnavailable,
    CourierPermissionDenied,
    CourierTaskNotFound,
    CourierTaskPage,
    CourierTaskResult,
    DispatchSnapshot,
    access_courier_contact,
    assign_courier,
    list_courier_tasks,
    load_dispatch_snapshot,
    transition_courier_task,
    unassign_courier,
)
from app.database import TenantTransaction
from app.tenancy import authorized_tenant_transaction

router = APIRouter(prefix="/api/v1/t/{tenant_id}", tags=["courier delivery"])
Transaction = Annotated[TenantTransaction, Depends(authorized_tenant_transaction)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]


def translate_courier_error(error: Exception) -> HTTPException:
    headers = {"Cache-Control": "no-store"}
    if isinstance(error, CourierPermissionDenied):
        return HTTPException(
            status_code=403, detail="Courier operation is not allowed", headers=headers
        )
    if isinstance(error, CourierTaskNotFound):
        return HTTPException(status_code=404, detail="Courier task not found", headers=headers)
    if isinstance(error, CourierConflict):
        return HTTPException(status_code=409, detail=str(error), headers=headers)
    if isinstance(error, CourierContactUnavailable):
        return HTTPException(
            status_code=409, detail="Delivery contact is unavailable", headers=headers
        )
    raise error


@router.get("/courier/dispatch", response_model=DispatchSnapshot)
async def dispatch_snapshot(response: Response, transaction: Transaction) -> DispatchSnapshot:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await load_dispatch_snapshot(transaction)
    except CourierPermissionDenied as error:
        raise translate_courier_error(error) from None


@router.put("/orders/{order_id}/courier-assignment", response_model=CourierAssignmentResult)
async def put_courier_assignment(
    command: CourierAssignmentCommand,
    order_id: UUID,
    response: Response,
    transaction: Transaction,
    idempotency_key: IdempotencyKey,
) -> CourierAssignmentResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await assign_courier(transaction, order_id, command.courier_user_id, idempotency_key)
    except (CourierPermissionDenied, CourierTaskNotFound, CourierConflict) as error:
        raise translate_courier_error(error) from None


@router.delete("/orders/{order_id}/courier-assignment", response_model=CourierAssignmentResult)
async def delete_courier_assignment(
    order_id: UUID,
    response: Response,
    transaction: Transaction,
    idempotency_key: IdempotencyKey,
) -> CourierAssignmentResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await unassign_courier(transaction, order_id, idempotency_key)
    except (CourierPermissionDenied, CourierTaskNotFound, CourierConflict) as error:
        raise translate_courier_error(error) from None


@router.get("/courier/tasks", response_model=CourierTaskPage)
async def courier_tasks(response: Response, transaction: Transaction) -> CourierTaskPage:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await list_courier_tasks(transaction)
    except CourierPermissionDenied as error:
        raise translate_courier_error(error) from None


@router.post("/courier/tasks/{task_id}/contact-access", response_model=CourierContactResult)
async def courier_contact_access(
    task_id: UUID,
    request: Request,
    response: Response,
    transaction: Transaction,
    idempotency_key: IdempotencyKey,
) -> CourierContactResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await access_courier_contact(
            transaction, task_id, idempotency_key, request.app.state.contact_cipher
        )
    except (
        CourierPermissionDenied,
        CourierTaskNotFound,
        CourierConflict,
        CourierContactUnavailable,
    ) as error:
        raise translate_courier_error(error) from None


@router.post("/courier/tasks/{task_id}/{action}", response_model=CourierTaskResult)
async def courier_task_transition(
    task_id: UUID,
    action: Literal["accept", "pickup", "deliver"],
    response: Response,
    transaction: Transaction,
    idempotency_key: IdempotencyKey,
) -> CourierTaskResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await transition_courier_task(transaction, task_id, action, idempotency_key)
    except (CourierPermissionDenied, CourierTaskNotFound, CourierConflict) as error:
        raise translate_courier_error(error) from None
