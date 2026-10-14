"""Public HTTP boundary for staff invitation redemption and sign-in."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.config import Settings
from app.database import Database
from app.rate_limit import enforce_rate_limit
from app.staff_auth import (
    InvalidStaffCredentials,
    InvalidStaffInvitation,
    StaffInvitationRedemption,
    StaffLoginCommand,
    StaffSession,
    login_staff,
    redeem_invitation,
)
from app.tenancy import database

router = APIRouter(prefix="/api/v1/auth/staff", tags=["staff authentication"])


def settings(request: Request) -> Settings:
    return request.app.state.config  # type: ignore[no-any-return]


@router.post("/login", response_model=StaffSession)
async def staff_login(
    command: StaffLoginCommand,
    request: Request,
    response: Response,
    db: Annotated[Database, Depends(database)],
    config: Annotated[Settings, Depends(settings)],
) -> StaffSession:
    response.headers["Cache-Control"] = "no-store"
    await enforce_rate_limit(
        request.app.state.redis,
        f"staff-login:{command.login_name}",
        limit=config.staff_auth_rate_limit,
        window_seconds=60,
        unavailable_detail="Authentication protection is unavailable",
        exceeded_detail="Too many authentication attempts",
    )
    try:
        return await login_staff(
            db,
            command,
            session_ttl_seconds=config.staff_session_ttl_seconds,
        )
    except InvalidStaffCredentials:
        raise HTTPException(
            status_code=401,
            detail="Invalid staff credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


@router.post("/invitations/redeem", response_model=StaffSession)
async def staff_invitation_redemption(
    command: StaffInvitationRedemption,
    request: Request,
    response: Response,
    db: Annotated[Database, Depends(database)],
    config: Annotated[Settings, Depends(settings)],
) -> StaffSession:
    response.headers["Cache-Control"] = "no-store"
    await enforce_rate_limit(
        request.app.state.redis,
        f"staff-invitation:{command.invitation_token}",
        limit=config.staff_auth_rate_limit,
        window_seconds=60,
        unavailable_detail="Authentication protection is unavailable",
        exceeded_detail="Too many authentication attempts",
    )
    try:
        return await redeem_invitation(
            db,
            command,
            session_ttl_seconds=config.staff_session_ttl_seconds,
        )
    except InvalidStaffInvitation:
        raise HTTPException(
            status_code=409, detail="Invitation is invalid or unavailable"
        ) from None
