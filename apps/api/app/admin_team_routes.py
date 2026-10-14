"""HTTP boundary for tenant staff administration."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from app.admin_team import (
    StaffMember,
    StaffMemberPatch,
    TeamConflict,
    TeamMemberNotFound,
    TeamPermissionDenied,
    TeamSnapshot,
    load_team,
    patch_staff_member,
)
from app.database import TenantTransaction
from app.tenancy import authorized_tenant_transaction

router = APIRouter(prefix="/api/v1/t/{tenant_id}/team", tags=["tenant team"])
Transaction = Annotated[TenantTransaction, Depends(authorized_tenant_transaction)]


def translate_team_error(error: Exception) -> HTTPException:
    if isinstance(error, TeamPermissionDenied):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, TeamMemberNotFound):
        return HTTPException(status_code=404, detail="Staff membership not found")
    if isinstance(error, TeamConflict):
        return HTTPException(status_code=409, detail=str(error))
    raise error


@router.get("", response_model=TeamSnapshot)
async def staff_team(response: Response, transaction: Transaction) -> TeamSnapshot:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await load_team(transaction)
    except TeamPermissionDenied as error:
        raise translate_team_error(error) from None


@router.patch("/{user_id}", response_model=StaffMember)
async def update_staff_member(
    user_id: UUID,
    command: StaffMemberPatch,
    response: Response,
    transaction: Transaction,
) -> StaffMember:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await patch_staff_member(transaction, user_id, command)
    except (TeamPermissionDenied, TeamMemberNotFound, TeamConflict) as error:
        raise translate_team_error(error) from None
