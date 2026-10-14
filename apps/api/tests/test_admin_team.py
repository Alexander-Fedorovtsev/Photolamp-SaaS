import os
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.bootstrap_runtime_role import RUNTIME_ROLE
from app.config import Settings
from app.main import create_app

TENANT_UFA = UUID("d0000000-0000-0000-0000-000000000001")
TENANT_KAZAN = UUID("d0000000-0000-0000-0000-000000000002")
DEMO_OWNER = UUID("d5000000-0000-0000-0000-000000000001")
DEMO_OPERATOR = UUID("d5000000-0000-0000-0000-000000000002")
DEMO_KAZAN_ADMIN = UUID("d5000000-0000-0000-0000-000000000004")


def database_urls() -> tuple[str, str]:
    defaults = Settings()
    configured = make_url(os.getenv("DATABASE_URL", defaults.database_url.get_secret_value()))
    runtime = configured.set(
        username=RUNTIME_ROLE,
        password="local-runtime-only",  # noqa: S106 - synthetic test credential
    ).render_as_string(hide_password=False)
    owner = os.getenv("DATABASE_OWNER_URL", defaults.migration_database_url())
    return runtime, owner


@pytest.mark.integration
async def test_owner_manages_privacy_safe_team_with_audit_and_role_guards() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    app = create_app(Settings(app_env="test", database_url=runtime_url, enable_demo_previews=True))
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                login = await client.post("/api/v1/auth/demo-session")
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                url = f"/api/v1/t/{TENANT_UFA}/team"

                assert (await client.get(url)).status_code == 401
                response = await client.get(url, headers=headers)
                assert response.status_code == 200
                assert response.headers["cache-control"] == "no-store"
                team = response.json()
                assert team["tenant_id"] == str(TENANT_UFA)
                assert team["can_manage"] is True
                assert len(team["members"]) == 3
                assert sum(member["is_current_user"] for member in team["members"]) == 1
                assert "phone" not in response.text

                updated = await client.patch(
                    f"{url}/{DEMO_OPERATOR}",
                    headers=headers,
                    json={
                        "display_name": "  Демо-координатор  ",
                        "role": "admin",
                        "state": "disabled",
                    },
                )
                assert updated.status_code == 200
                assert updated.json()["display_name"] == "Демо-координатор"
                assert updated.json()["role"] == "admin"
                assert updated.json()["state"] == "disabled"

                self_change = await client.patch(
                    f"{url}/{DEMO_OWNER}",
                    headers=headers,
                    json={"role": "operator"},
                )
                assert self_change.status_code == 409
                assert self_change.json()["detail"] == (
                    "You cannot change your own role or access state"
                )
                foreign = await client.patch(
                    f"{url}/{DEMO_KAZAN_ADMIN}",
                    headers=headers,
                    json={"state": "disabled"},
                )
                assert foreign.status_code == 404

                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE tenant_memberships SET state = 'invited' "
                            "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                        ),
                        {"tenant_id": TENANT_UFA, "user_id": DEMO_OPERATOR},
                    )
                premature_activation = await client.patch(
                    f"{url}/{DEMO_OPERATOR}",
                    headers=headers,
                    json={"state": "active"},
                )
                assert premature_activation.status_code == 409
                assert premature_activation.json()["detail"] == (
                    "Invited membership requires a production activation flow"
                )

                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE tenant_memberships SET state = 'disabled' "
                            "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                        ),
                        {"tenant_id": TENANT_UFA, "user_id": DEMO_OPERATOR},
                    )
                    audit = (
                        await connection.execute(
                            text(
                                "SELECT action, actor_user_id, changes FROM audit_events "
                                "WHERE tenant_id = :tenant_id AND entity_id = :entity_id "
                                "ORDER BY occurred_at DESC LIMIT 1"
                            ),
                            {"tenant_id": TENANT_UFA, "entity_id": DEMO_OPERATOR},
                        )
                    ).one()
                    assert audit.action == "staff.updated"
                    assert audit.actor_user_id == DEMO_OWNER
                    assert audit.changes["before"]["role"] == "operator"
                    assert audit.changes["after"]["role"] == "admin"
                    assert "phone" not in str(audit.changes)
                    await connection.execute(
                        text(
                            "UPDATE tenant_memberships SET role = 'admin' "
                            "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                        ),
                        {"tenant_id": TENANT_UFA, "user_id": DEMO_OWNER},
                    )

                admin_view = await client.get(url, headers=headers)
                assert admin_view.status_code == 200
                assert admin_view.json()["can_manage"] is False
                admin_write = await client.patch(
                    f"{url}/{DEMO_OPERATOR}",
                    headers=headers,
                    json={"state": "active"},
                )
                assert admin_write.status_code == 403
    finally:
        async with owner.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE tenant_memberships SET display_name = 'Демо-владелец', "
                    "role = 'owner', state = 'active', updated_at = now() "
                    "WHERE tenant_id = :tenant_id AND user_id = :owner_id"
                ),
                {"tenant_id": TENANT_UFA, "owner_id": DEMO_OWNER},
            )
            await connection.execute(
                text(
                    "UPDATE tenant_memberships SET display_name = 'Демо-оператор', "
                    "role = 'operator', state = 'active', updated_at = now() "
                    "WHERE tenant_id = :tenant_id AND user_id = :operator_id"
                ),
                {"tenant_id": TENANT_UFA, "operator_id": DEMO_OPERATOR},
            )
            await connection.execute(
                text(
                    "DELETE FROM audit_events WHERE tenant_id = :tenant_id "
                    "AND entity_id = :entity_id AND action = 'staff.updated'"
                ),
                {"tenant_id": TENANT_UFA, "entity_id": DEMO_OPERATOR},
            )
        await owner.dispose()
