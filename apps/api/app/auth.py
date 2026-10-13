"""Environment-gated synthetic staff sessions for the demo workspace."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from app.database import Database


class DemoSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: str
    token_type: str = "bearer"  # noqa: S105 - protocol type, not a credential
    expires_at: datetime
    demo_only: bool = True


async def issue_demo_session(database: Database) -> DemoSession:
    token = f"pl_{secrets.token_urlsafe(36)}"
    expires_at = datetime.now(UTC) + timedelta(hours=4)
    await database.issue_demo_session(
        session_id=uuid4(),
        token_hash=hashlib.sha256(token.encode()).digest(),
        expires_at=expires_at,
    )
    return DemoSession(access_token=token, expires_at=expires_at)


class SessionRevoked(BaseModel):
    model_config = ConfigDict(frozen=True)

    revoked: bool


async def revoke_session(database: Database, token_hash: bytes) -> SessionRevoked:
    return SessionRevoked(revoked=await database.revoke_session(token_hash))
