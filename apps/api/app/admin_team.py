"""Tenant-scoped staff directory and least-privilege role administration."""

import hashlib
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text

from app.audit import record_audit
from app.database import TenantTransaction

StaffRole = Literal["owner", "admin", "operator", "courier"]
MembershipState = Literal["invited", "active", "disabled"]


class StaffMember(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: UUID
    display_name: str
    role: StaffRole
    state: MembershipState
    is_current_user: bool
    created_at: datetime
    updated_at: datetime


class TeamSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    can_manage: bool
    members: list[StaffMember]


class StaffMemberPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    role: StaffRole | None = None
    state: Literal["active", "disabled"] | None = None

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("display_name must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_change(self) -> "StaffMemberPatch":
        if self.display_name is None and self.role is None and self.state is None:
            raise ValueError("at least one staff field is required")
        return self


class TeamPermissionDenied(Exception):
    """The current membership cannot view or administer the team."""


class TeamMemberNotFound(Exception):
    """The target membership is outside the authorized tenant or missing."""


class TeamConflict(Exception):
    """The requested membership change would violate an access invariant."""


def require_team_reader(transaction: TenantTransaction) -> None:
    if transaction.role not in {"owner", "admin"}:
        raise TeamPermissionDenied("Owner or admin role required")


def require_team_manager(transaction: TenantTransaction) -> None:
    if transaction.role != "owner":
        raise TeamPermissionDenied("Owner role required")


def _staff_member(row: object, current_user_id: UUID) -> StaffMember:
    mapping = row._mapping  # type: ignore[attr-defined]
    return StaffMember(
        user_id=mapping["user_id"],
        display_name=mapping["display_name"],
        role=mapping["role"],
        state=mapping["state"],
        is_current_user=mapping["user_id"] == current_user_id,
        created_at=mapping["created_at"],
        updated_at=mapping["updated_at"],
    )


async def load_team(transaction: TenantTransaction) -> TeamSnapshot:
    require_team_reader(transaction)
    rows = (
        await transaction.session.execute(
            text(
                "SELECT user_id, display_name, role, state, created_at, updated_at "
                "FROM tenant_memberships WHERE tenant_id = :tenant_id "
                "ORDER BY CASE state WHEN 'active' THEN 0 WHEN 'invited' THEN 1 ELSE 2 END, "
                "CASE role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 "
                "WHEN 'operator' THEN 2 ELSE 3 END, display_name, user_id"
            ),
            {"tenant_id": transaction.tenant_id},
        )
    ).all()
    return TeamSnapshot(
        tenant_id=transaction.tenant_id,
        can_manage=transaction.role == "owner",
        members=[_staff_member(row, transaction.user_id) for row in rows],
    )


async def patch_staff_member(
    transaction: TenantTransaction, user_id: UUID, command: StaffMemberPatch
) -> StaffMember:
    require_team_manager(transaction)
    lock_key = int.from_bytes(
        hashlib.sha256(b"tenant-team" + transaction.tenant_id.bytes).digest()[:8],
        "big",
        signed=True,
    )
    await transaction.session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
    )
    actor = (
        await transaction.session.execute(
            text(
                "SELECT role, state FROM tenant_memberships "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id FOR UPDATE"
            ),
            {"tenant_id": transaction.tenant_id, "user_id": transaction.user_id},
        )
    ).one_or_none()
    if actor is None or actor.role != "owner" or actor.state != "active":
        raise TeamPermissionDenied("Owner role required")
    row = (
        await transaction.session.execute(
            text(
                "SELECT user_id, display_name, role, state, created_at, updated_at "
                "FROM tenant_memberships WHERE tenant_id = :tenant_id AND user_id = :user_id "
                "FOR UPDATE"
            ),
            {"tenant_id": transaction.tenant_id, "user_id": user_id},
        )
    ).one_or_none()
    if row is None:
        raise TeamMemberNotFound

    current = _staff_member(row, transaction.user_id)
    changes = command.model_dump(exclude_none=True)
    if user_id == transaction.user_id and ({"role", "state"} & changes.keys()):
        raise TeamConflict("You cannot change your own role or access state")

    next_role = command.role or current.role
    next_state = command.state or current.state
    if current.state == "invited" and next_state == "active":
        raise TeamConflict("Invited membership requires a production activation flow")
    if (
        current.role == "owner"
        and current.state == "active"
        and (next_role != "owner" or next_state != "active")
    ):
        active_owners = await transaction.session.scalar(
            text(
                "SELECT count(*) FROM tenant_memberships WHERE tenant_id = :tenant_id "
                "AND role = 'owner' AND state = 'active' AND user_id <> :user_id"
            ),
            {"tenant_id": transaction.tenant_id, "user_id": user_id},
        )
        if int(active_owners or 0) == 0:
            raise TeamConflict("At least one active owner is required")

    next_values = {
        "display_name": command.display_name or current.display_name,
        "role": next_role,
        "state": next_state,
    }
    before = {
        "display_name": current.display_name,
        "role": current.role,
        "state": current.state,
    }
    if next_values == before:
        return current

    updated = (
        await transaction.session.execute(
            text(
                "UPDATE tenant_memberships SET display_name = :display_name, role = :role, "
                "state = :state, updated_at = now() WHERE tenant_id = :tenant_id "
                "AND user_id = :user_id RETURNING user_id, display_name, role, state, "
                "created_at, updated_at"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "user_id": user_id,
                **next_values,
            },
        )
    ).one()
    await record_audit(
        transaction,
        "staff.updated",
        "tenant_membership",
        user_id,
        {"before": before, "after": next_values},
    )
    return _staff_member(updated, transaction.user_id)
