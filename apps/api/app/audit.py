"""Shared tenant audit writer for administrative commands."""

import json
from uuid import UUID, uuid4

from sqlalchemy import text

from app.database import TenantTransaction


async def record_audit(
    transaction: TenantTransaction,
    action: str,
    entity_type: str,
    entity_id: UUID,
    changes: dict[str, object],
) -> None:
    await transaction.session.execute(
        text(
            "INSERT INTO audit_events "
            "(id, tenant_id, actor_user_id, action, entity_type, entity_id, changes) "
            "VALUES (:id, :tenant_id, :actor_user_id, :action, :entity_type, "
            ":entity_id, CAST(:changes AS jsonb))"
        ),
        {
            "id": uuid4(),
            "tenant_id": transaction.tenant_id,
            "actor_user_id": transaction.user_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "changes": json.dumps(changes, default=str, ensure_ascii=False),
        },
    )
