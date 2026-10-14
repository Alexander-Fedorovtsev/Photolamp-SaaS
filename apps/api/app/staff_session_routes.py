"""Authenticated HTTP boundary for a staff member's own sessions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from app.database import Database
from app.staff_sessions import (
    InvalidStaffSession,
    StaffSessionNotFound,
    StaffSessionRevoked,
    StaffSessionsRevoked,
    StaffSessionSummary,
    list_sessions,
    revoke_all_sessions,
    revoke_one_session,
)
from app.tenancy import database, session_digest

router = APIRouter(prefix="/api/v1/auth/staff/sessions", tags=["staff authentication"])
SessionDigest = Annotated[bytes, Depends(session_digest)]
DatabaseDependency = Annotated[Database, Depends(database)]


def invalid_session() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Valid session required",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.get("", response_model=list[StaffSessionSummary])
async def staff_sessions(
    response: Response, digest: SessionDigest, db: DatabaseDependency
) -> list[StaffSessionSummary]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await list_sessions(db, digest)
    except InvalidStaffSession:
        raise invalid_session() from None


@router.delete("/{session_id}", response_model=StaffSessionRevoked)
async def revoke_staff_session(
    session_id: UUID,
    response: Response,
    digest: SessionDigest,
    db: DatabaseDependency,
) -> StaffSessionRevoked:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await revoke_one_session(db, digest, session_id)
    except InvalidStaffSession:
        raise invalid_session() from None
    except StaffSessionNotFound:
        raise HTTPException(status_code=404, detail="Session not found") from None


@router.delete("", response_model=StaffSessionsRevoked)
async def revoke_every_staff_session(
    response: Response, digest: SessionDigest, db: DatabaseDependency
) -> StaffSessionsRevoked:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await revoke_all_sessions(db, digest)
    except InvalidStaffSession:
        raise invalid_session() from None
