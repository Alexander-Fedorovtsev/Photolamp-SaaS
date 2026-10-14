"""HTTP boundary for tenant staff administration."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.admin_team import (
    StaffInvitationCreate,
    StaffInvitationCreated,
    StaffInvitationRevoked,
    StaffInvitationSummary,
    StaffMember,
    StaffMemberPatch,
    TeamConflict,
    TeamMemberNotFound,
    TeamPermissionDenied,
    TeamSnapshot,
    create_staff_invitation,
    list_staff_invitations,
    load_team,
    patch_staff_member,
    revoke_staff_invitation,
)
from app.config import Settings
from app.database import TenantTransaction
from app.tenancy import authorized_tenant_transaction

router = APIRouter(prefix="/api/v1/t/{tenant_id}/team", tags=["tenant team"])
Transaction = Annotated[TenantTransaction, Depends(authorized_tenant_transaction)]


def settings(request: Request) -> Settings:
    return request.app.state.config  # type: ignore[no-any-return]


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


@router.get("/invitations", response_model=list[StaffInvitationSummary])
async def staff_invitations(
    response: Response, transaction: Transaction
) -> list[StaffInvitationSummary]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await list_staff_invitations(transaction)
    except TeamPermissionDenied as error:
        raise translate_team_error(error) from None


@router.post("/invitations", response_model=StaffInvitationCreated, status_code=201)
async def new_staff_invitation(
    command: StaffInvitationCreate,
    request: Request,
    response: Response,
    transaction: Transaction,
    config: Annotated[Settings, Depends(settings)],
) -> StaffInvitationCreated:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await create_staff_invitation(
            transaction,
            command,
            ttl_seconds=config.staff_invitation_ttl_seconds,
        )
    except (TeamPermissionDenied, TeamConflict) as error:
        raise translate_team_error(error) from None


@router.delete("/invitations/{invitation_id}", response_model=StaffInvitationRevoked)
async def remove_staff_invitation(
    invitation_id: UUID,
    response: Response,
    transaction: Transaction,
) -> StaffInvitationRevoked:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await revoke_staff_invitation(transaction, invitation_id)
    except (TeamPermissionDenied, TeamMemberNotFound, TeamConflict) as error:
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
