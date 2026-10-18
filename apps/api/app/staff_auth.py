"""Staff invitation redemption and password-backed session authentication."""

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from app.database import Database
from app.staff_sessions import annotate_session

LOGIN_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{3,31}$")
INVITATION_TOKEN_PATTERN = re.compile(r"^pli_[A-Za-z0-9_-]{32,128}$")
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LENGTH = 32


class StaffLoginCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_name: Annotated[str, Field(min_length=4, max_length=32)]
    password: Annotated[str, Field(min_length=12, max_length=128)]

    @field_validator("login_name")
    @classmethod
    def normalize_login(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not LOGIN_PATTERN.fullmatch(normalized):
            raise ValueError("login_name has an invalid format")
        return normalized


class StaffInvitationRedemption(StaffLoginCommand):
    invitation_token: Annotated[str, Field(min_length=36, max_length=132)]

    @field_validator("invitation_token")
    @classmethod
    def validate_invitation_token(cls, value: str) -> str:
        if not INVITATION_TOKEN_PATTERN.fullmatch(value):
            raise ValueError("invitation_token has an invalid format")
        return value


class StaffSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: str
    token_type: str = "bearer"  # noqa: S105 - protocol type, not a credential
    expires_at: datetime
    demo_only: bool = False


class InvalidStaffCredentials(Exception):
    """The login is unknown, locked, or the password does not match."""


class InvalidStaffInvitation(Exception):
    """The invitation cannot be redeemed or the requested login is unavailable."""


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_b64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    actual_salt = salt or secrets.token_bytes(16)
    derived = Scrypt(
        salt=actual_salt,
        length=SCRYPT_LENGTH,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    ).derive(password.encode())
    return f"$scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(actual_salt)}${_b64(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = Scrypt(
            salt=_decode_b64(salt),
            length=len(_decode_b64(expected)),
            n=int(n),
            r=int(r),
            p=int(p),
        ).derive(password.encode())
        return hmac.compare_digest(actual, _decode_b64(expected))
    except (ValueError, TypeError):
        return False


DUMMY_PASSWORD_HASH = hash_password("photolamp-invalid-credential", salt=bytes(16))


def _session_material(ttl_seconds: int) -> tuple[UUID, str, bytes, datetime]:
    token = f"pl_{secrets.token_urlsafe(36)}"
    return (
        uuid4(),
        token,
        hashlib.sha256(token.encode()).digest(),
        datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )


async def redeem_invitation(
    database: Database,
    command: StaffInvitationRedemption,
    *,
    session_ttl_seconds: int,
    client_label: str,
) -> StaffSession:
    password_hash = await asyncio.to_thread(hash_password, command.password)
    session_id, token, token_hash, expires_at = _session_material(session_ttl_seconds)
    invitation_hash = hashlib.sha256(command.invitation_token.encode()).digest()
    async with database.transaction() as session:
        redeemed = (
            await session.execute(
                text(
                    "SELECT user_id, tenant_id, expires_at FROM redeem_staff_invitation("
                    ":invitation_hash, :login_name, :password_hash, :session_id, "
                    ":token_hash, :expires_at)"
                ),
                {
                    "invitation_hash": invitation_hash,
                    "login_name": command.login_name,
                    "password_hash": password_hash,
                    "session_id": session_id,
                    "token_hash": token_hash,
                    "expires_at": expires_at,
                },
            )
        ).one_or_none()
        if redeemed is not None:
            await annotate_session(
                session,
                token_hash,
                client_label=client_label,
                session_kind="staff",
            )
    if redeemed is None:
        raise InvalidStaffInvitation
    return StaffSession(access_token=token, expires_at=redeemed.expires_at)


async def login_staff(
    database: Database,
    command: StaffLoginCommand,
    *,
    session_ttl_seconds: int,
    client_label: str,
) -> StaffSession:
    async with database.transaction() as session:
        credential = (
            await session.execute(
                text(
                    "SELECT user_id, password_hash, failed_attempts, locked_until "
                    "FROM lookup_staff_login(:login_name)"
                ),
                {"login_name": command.login_name},
            )
        ).one_or_none()
    encoded = credential.password_hash if credential is not None else DUMMY_PASSWORD_HASH
    password_matches = await asyncio.to_thread(verify_password, command.password, encoded)
    now = datetime.now(UTC)
    is_locked = (
        credential is not None
        and credential.locked_until is not None
        and credential.locked_until > now
    )
    if credential is None or not password_matches or is_locked:
        if not is_locked:
            async with database.transaction() as session:
                await session.execute(
                    text("SELECT register_staff_login_failure(:login_name)"),
                    {"login_name": command.login_name},
                )
        raise InvalidStaffCredentials

    session_id, token, token_hash, expires_at = _session_material(session_ttl_seconds)
    async with database.transaction() as session:
        user_id = await session.scalar(
            text(
                "SELECT issue_staff_login_session("
                ":login_name, :session_id, :token_hash, :expires_at)"
            ),
            {
                "login_name": command.login_name,
                "session_id": session_id,
                "token_hash": token_hash,
                "expires_at": expires_at,
            },
        )
        if user_id is not None:
            await annotate_session(
                session,
                token_hash,
                client_label=client_label,
                session_kind="staff",
            )
    if user_id is None:
        raise InvalidStaffCredentials
    return StaffSession(access_token=token, expires_at=expires_at)


class StaffInvitationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invitation_token: Annotated[str, Field(pattern=r"^pli_[A-Za-z0-9_-]{32,128}$")]


class StaffInvitationPreview(BaseModel):
    model_config = ConfigDict(frozen=True)
    organization_name: str
    display_label: str
    role: str
    expires_at: datetime


async def preview_invitation(database: Database, token: str) -> StaffInvitationPreview:
    async with database.transaction() as session:
        row = (
            await session.execute(
                text("SELECT * FROM preview_staff_invitation(:token_hash)"),
                {"token_hash": hashlib.sha256(token.encode()).digest()},
            )
        ).one_or_none()
    if row is None:
        raise InvalidStaffInvitation
    return StaffInvitationPreview.model_validate(dict(row._mapping))
