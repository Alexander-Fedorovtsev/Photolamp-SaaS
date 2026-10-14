"""Privacy-minimized staff session metadata, inventory and revocation."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Database


class StaffSessionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: UUID
    client_label: str = Field(min_length=1, max_length=80)
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    is_current: bool


class StaffSessionRevoked(BaseModel):
    model_config = ConfigDict(frozen=True)

    revoked: bool = True
    revoked_current: bool


class StaffSessionsRevoked(BaseModel):
    model_config = ConfigDict(frozen=True)

    revoked_count: int = Field(ge=1)


class InvalidStaffSession(Exception):
    """The current opaque session is missing, expired or revoked."""


class StaffSessionNotFound(Exception):
    """The requested session does not belong to the current identity."""


def coarse_client_label(user_agent: str | None) -> str:
    """Reduce a user agent to a non-identifying browser and OS label."""
    raw = (user_agent or "").lower()
    if any(marker in raw for marker in ("curl/", "httpx/", "python-", "postman", "undici")):
        return "API client"
    if "edg/" in raw:
        browser = "Edge"
    elif "opr/" in raw or "opera" in raw:
        browser = "Opera"
    elif "chrome/" in raw or "crios/" in raw:
        browser = "Chrome"
    elif "firefox/" in raw or "fxios/" in raw:
        browser = "Firefox"
    elif "safari/" in raw:
        browser = "Safari"
    else:
        browser = "Browser"

    if "android" in raw:
        platform = "Android"
    elif any(marker in raw for marker in ("iphone", "ipad", "ipod")):
        platform = "iOS"
    elif "windows" in raw:
        platform = "Windows"
    elif "macintosh" in raw or "mac os" in raw:
        platform = "macOS"
    elif "linux" in raw:
        platform = "Linux"
    else:
        platform = ""
    return f"{browser} · {platform}" if platform else browser


async def annotate_session(
    session: AsyncSession,
    token_hash: bytes,
    *,
    client_label: str,
    session_kind: str,
) -> None:
    annotated = await session.scalar(
        text(
            "SELECT COALESCE(set_auth_session_metadata("
            ":token_hash, :client_label, :session_kind), false)"
        ),
        {
            "token_hash": token_hash,
            "client_label": client_label,
            "session_kind": session_kind,
        },
    )
    if not annotated:
        raise RuntimeError("Issued staff session could not be annotated")


async def list_sessions(database: Database, token_hash: bytes) -> list[StaffSessionSummary]:
    async with database.transaction() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT session_id, client_label, created_at, last_seen_at, "
                    "expires_at, is_current FROM list_staff_sessions(:token_hash)"
                ),
                {"token_hash": token_hash},
            )
        ).all()
    if not rows:
        raise InvalidStaffSession
    return [StaffSessionSummary.model_validate(dict(row._mapping)) for row in rows]


async def revoke_one_session(
    database: Database, token_hash: bytes, session_id: UUID
) -> StaffSessionRevoked:
    async with database.transaction() as session:
        status = await session.scalar(
            text("SELECT revoke_staff_session(:token_hash, :session_id)"),
            {"token_hash": token_hash, "session_id": session_id},
        )
    if status == -1:
        raise InvalidStaffSession
    if status == -2 or status is None:
        raise StaffSessionNotFound
    return StaffSessionRevoked(revoked_current=status == 1)


async def revoke_all_sessions(database: Database, token_hash: bytes) -> StaffSessionsRevoked:
    async with database.transaction() as session:
        revoked_count = await session.scalar(
            text("SELECT revoke_all_staff_sessions(:token_hash)"),
            {"token_hash": token_hash},
        )
    if revoked_count is None:
        raise InvalidStaffSession
    return StaffSessionsRevoked(revoked_count=int(revoked_count))
