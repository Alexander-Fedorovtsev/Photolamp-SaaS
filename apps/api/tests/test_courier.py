import os
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.bootstrap_runtime_role import RUNTIME_ROLE
from app.capabilities import capability_digest
from app.config import Settings
from app.main import create_app

TENANT_UFA = UUID("d0000000-0000-0000-0000-000000000001")
TENANT_KAZAN = UUID("d0000000-0000-0000-0000-000000000002")
BRANCH_UFA = UUID("d1000000-0000-0000-0000-000000000001")
MODEL_UFA = UUID("d2000000-0000-0000-0000-000000000001")
COURIER_USER = UUID("d5000000-0000-0000-0000-000000000003")
OTHER_USER = UUID("d5000000-0000-0000-0000-000000000002")


def database_urls() -> tuple[str, str]:
    defaults = Settings()
    configured = make_url(os.getenv("DATABASE_URL", defaults.database_url.get_secret_value()))
    runtime = configured.set(
        username=RUNTIME_ROLE,
        password="local-runtime-only",  # noqa: S106 - synthetic test credential
    ).render_as_string(hide_password=False)
    owner = os.getenv("DATABASE_OWNER_URL", defaults.migration_database_url())
    return runtime, owner


async def clear_data(owner: AsyncEngine) -> None:
    async with owner.begin() as connection:
        await connection.execute(
            text("UPDATE checkout_sessions SET customer_id = NULL WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        await connection.execute(
            text("UPDATE rental_allocations SET order_id = NULL WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        for table in (
            "customer_auth_challenges",
            "customer_sessions",
            "order_events",
            "orders",
            "rental_allocations",
            "checkout_sessions",
            "customers",
        ):
            await connection.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :tenant_id"),  # noqa: S608
                {"tenant_id": TENANT_UFA},
            )
        await connection.execute(
            text(
                "UPDATE assets SET state = CASE asset_code "
                "WHEN 'DEMO-UFA-02' THEN 'inspection' ELSE 'available' END, updated_at = now() "
                "WHERE tenant_id = :tenant_id AND asset_code LIKE 'DEMO-UFA-%'"
            ),
            {"tenant_id": TENANT_UFA},
        )
        await connection.execute(
            text(
                "DELETE FROM auth_sessions WHERE user_id IN (:courier_user, :other_user) "
                "AND session_kind = 'staff'"
            ),
            {"courier_user": COURIER_USER, "other_user": OTHER_USER},
        )
        await connection.execute(
            text(
                "UPDATE tenant_memberships SET role = CASE WHEN user_id = :other_user "
                "THEN 'operator' ELSE role END, state = 'active' "
                "WHERE tenant_id = :tenant_id AND user_id IN (:courier_user, :other_user)"
            ),
            {
                "tenant_id": TENANT_UFA,
                "courier_user": COURIER_USER,
                "other_user": OTHER_USER,
            },
        )


async def issue_staff_session(owner: AsyncEngine, user_id: UUID, token: str) -> None:
    async with owner.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO auth_sessions "
                "(id, user_id, token_hash, expires_at, client_label, session_kind) "
                "VALUES (:id, :user_id, :token_hash, :expires_at, 'Courier test', 'staff')"
            ),
            {
                "id": uuid4(),
                "user_id": user_id,
                "token_hash": capability_digest(token),
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
            },
        )


@pytest.mark.integration
async def test_courier_sees_only_assigned_delivery_and_completes_it() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    settings = Settings(app_env="test", database_url=runtime_url, enable_demo_previews=True)
    app = create_app(settings)
    courier_token = "pl_synthetic_courier_session_token_0001"  # noqa: S105
    other_token = "pl_synthetic_other_courier_token_0001"  # noqa: S105
    start = date.today() + timedelta(days=75)
    command = {
        "phone_e164": "+70000000000",
        "recipient_name": "Тестовый клиент",
        "delivery_address": "Тестовый адрес",
        "model_id": str(MODEL_UFA),
        "branch_id": str(BRANCH_UFA),
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=2)).isoformat(),
        "fulfillment": "delivery",
    }
    try:
        await clear_data(owner)
        await issue_staff_session(owner, COURIER_USER, courier_token)
        await issue_staff_session(owner, OTHER_USER, other_token)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                login = await client.post("/api/v1/auth/demo-session")
                staff = {"Authorization": f"Bearer {login.json()['access_token']}"}
                courier = {"Authorization": f"Bearer {courier_token}"}
                other = {"Authorization": f"Bearer {other_token}"}
                created = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/phone",
                    headers={**staff, "Idempotency-Key": "courier-order-create-0001"},
                    json=command,
                )
                assert created.status_code == 201
                order_id = created.json()["order"]["order_id"]

                dispatch = await client.get(
                    f"/api/v1/t/{TENANT_UFA}/courier/dispatch", headers=staff
                )
                assert dispatch.status_code == 200
                assert dispatch.headers["cache-control"] == "no-store"
                assert dispatch.json()["couriers"] == [
                    {"user_id": str(COURIER_USER), "display_name": "Демо-курьер"}
                ]
                assert dispatch.json()["assignments"] == []
                assert "+70000000000" not in dispatch.text
                assert "Тестовый адрес" not in dispatch.text

                before_assignment = await client.get(
                    f"/api/v1/t/{TENANT_UFA}/courier/tasks", headers=courier
                )
                assert before_assignment.status_code == 200
                assert before_assignment.json() == {"items": []}
                assigned = await client.put(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/courier-assignment",
                    headers={**staff, "Idempotency-Key": "courier-assign-order-0001"},
                    json={"courier_user_id": str(COURIER_USER)},
                )
                assert assigned.status_code == 200
                assert assigned.json()["replayed"] is False
                task_id = assigned.json()["assignment"]["task_id"]
                replay = await client.put(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/courier-assignment",
                    headers={**staff, "Idempotency-Key": "courier-assign-order-0001"},
                    json={"courier_user_id": str(COURIER_USER)},
                )
                assert replay.status_code == 200
                assert replay.json()["replayed"] is True

                task_page = await client.get(
                    f"/api/v1/t/{TENANT_UFA}/courier/tasks", headers=courier
                )
                assert task_page.status_code == 200
                assert len(task_page.json()["items"]) == 1
                task = task_page.json()["items"][0]
                assert task["task_id"] == task_id
                assert task["state"] == "assigned"
                assert task["snapshot"]["model_name"]
                assert "+70000000000" not in task_page.text
                assert "Тестовый адрес" not in task_page.text

                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE tenant_memberships SET role = 'courier' "
                            "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                        ),
                        {"tenant_id": TENANT_UFA, "user_id": OTHER_USER},
                    )
                other_tasks = await client.get(
                    f"/api/v1/t/{TENANT_UFA}/courier/tasks", headers=other
                )
                assert other_tasks.status_code == 200
                assert other_tasks.json() == {"items": []}
                other_direct = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/courier/tasks/{task_id}/accept",
                    headers={**other, "Idempotency-Key": "other-courier-accept-0001"},
                )
                assert other_direct.status_code == 404
                cross_tenant = await client.get(
                    f"/api/v1/t/{TENANT_KAZAN}/courier/tasks", headers=courier
                )
                assert cross_tenant.status_code == 404

                premature_contact = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/courier/tasks/{task_id}/contact-access",
                    headers={**courier, "Idempotency-Key": "courier-contact-early-0001"},
                )
                assert premature_contact.status_code == 409
                accepted = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/courier/tasks/{task_id}/accept",
                    headers={**courier, "Idempotency-Key": "courier-accept-task-0001"},
                )
                assert accepted.status_code == 200
                assert accepted.json()["task"]["state"] == "accepted"
                contact = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/courier/tasks/{task_id}/contact-access",
                    headers={**courier, "Idempotency-Key": "courier-contact-task-0001"},
                )
                assert contact.status_code == 200
                assert contact.headers["cache-control"] == "no-store"
                assert contact.json()["contact"] == {
                    "phone_e164": "+70000000000",
                    "recipient_name": "Тестовый клиент",
                    "delivery_address": "Тестовый адрес",
                }
                picked_up = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/courier/tasks/{task_id}/pickup",
                    headers={**courier, "Idempotency-Key": "courier-pickup-task-0001"},
                )
                assert picked_up.status_code == 200
                assert picked_up.json()["task"]["state"] == "picked_up"
                delivered = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/courier/tasks/{task_id}/deliver",
                    headers={**courier, "Idempotency-Key": "courier-deliver-task-0001"},
                )
                assert delivered.status_code == 200
                assert delivered.json()["task"]["state"] == "delivered"
                assert delivered.json()["task"]["delivered_at"] is not None
                contact_after_delivery = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/courier/tasks/{task_id}/contact-access",
                    headers={**courier, "Idempotency-Key": "courier-contact-late-0001"},
                )
                assert contact_after_delivery.status_code == 409

                async with owner.begin() as connection:
                    lifecycle = (
                        await connection.execute(
                            text(
                                "SELECT o.state, a.state AS asset_state FROM orders o "
                                "JOIN rental_allocations ra ON ra.id = o.allocation_id "
                                "JOIN assets a ON a.id = ra.asset_id WHERE o.id = :order_id"
                            ),
                            {"order_id": UUID(order_id)},
                        )
                    ).one()
                    events: list[str] = list(
                        (
                            await connection.execute(
                                text(
                                    "SELECT event_type FROM order_events "
                                    "WHERE order_id = :order_id "
                                    "ORDER BY happened_at, id"
                                ),
                                {"order_id": UUID(order_id)},
                            )
                        ).scalars()
                    )
                assert lifecycle == ("in_progress", "rented")
                assert events == [
                    "phone_order_created",
                    "courier_assigned",
                    "courier_accepted",
                    "courier_contact_accessed",
                    "courier_picked_up",
                    "courier_delivered",
                ]
    finally:
        await clear_data(owner)
        await owner.dispose()
