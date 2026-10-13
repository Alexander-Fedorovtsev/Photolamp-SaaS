"""Opaque application sessions and immutable tenant authorization context."""

import hashlib
import re
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from app.database import Database, TenantAccessDenied

bearer = HTTPBearer(auto_error=False)
TOKEN_PATTERN = re.compile(r"^pl_[A-Za-z0-9_-]{32,128}$")


class TenantSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    slug: str
    display_name: str
    timezone: str
    role: str


class TenantContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    slug: str
    display_name: str
    timezone: str
    role: str


def token_digest(credentials: HTTPAuthorizationCredentials | None) -> bytes:
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not TOKEN_PATTERN.fullmatch(credentials.credentials)
    ):
        raise HTTPException(
            status_code=401,
            detail="Valid session required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return hashlib.sha256(credentials.credentials.encode()).digest()


async def session_digest(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> bytes:
    return token_digest(credentials)


def database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


async def list_memberships(database: Database, digest: bytes) -> list[TenantSummary]:
    return [TenantSummary.model_validate(row) for row in await database.list_tenants(digest)]


async def get_tenant_context(database: Database, digest: bytes, tenant_id: UUID) -> TenantContext:
    try:
        async with database.tenant_transaction(digest, tenant_id) as transaction:
            row = (
                await transaction.session.execute(
                    text(
                        "SELECT id AS tenant_id, slug, display_name, timezone "
                        "FROM tenants WHERE id = :tenant_id"
                    ),
                    {"tenant_id": tenant_id},
                )
            ).one()
            return TenantContext(**dict(row._mapping), role=transaction.role)
    except TenantAccessDenied:
        raise HTTPException(status_code=404, detail="Tenant not found") from None
