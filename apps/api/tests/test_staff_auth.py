import asyncio
import os
from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import bindparam, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.bootstrap_runtime_role import RUNTIME_ROLE
from app.config import Settings
from app.main import create_app
from app.staff_auth import hash_password, verify_password

TENANT_UFA = UUID("d0000000-0000-0000-0000-000000000001")
TENANT_KAZAN = UUID("d0000000-0000-0000-0000-000000000002")


def database_urls() -> tuple[str, str]:
    defaults = Settings()
    configured = make_url(os.getenv("DATABASE_URL", defaults.database_url.get_secret_value()))
    runtime = configured.set(
        username=RUNTIME_ROLE,
        password="local-runtime-only",  # noqa: S106 - synthetic test credential
    ).render_as_string(hide_password=False)
    owner = os.getenv("DATABASE_OWNER_URL", defaults.migration_database_url())
    return runtime, owner


def test_scrypt_password_hash_uses_random_salt_and_rejects_wrong_password() -> None:
    first = hash_password("correct horse battery")
    second = hash_password("correct horse battery")
    assert first.startswith("$scrypt$")
    assert first != second
    assert verify_password("correct horse battery", first)
    assert not verify_password("wrong horse battery", first)
    assert not verify_password("correct horse battery", "$broken$")


@pytest.mark.integration
async def test_staff_invitation_redemption_login_revocation_and_expiry() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    app = create_app(Settings(app_env="test", database_url=runtime_url, enable_demo_previews=True))
    created_user_ids: list[UUID] = []
    invitation_ids: list[UUID] = []
    login_name = f"invitee{uuid4().hex[:10]}"
    password = "synthetic-password-42"  # noqa: S105 - test credential
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                demo = await client.post("/api/v1/auth/demo-session")
                owner_headers = {"Authorization": f"Bearer {demo.json()['access_token']}"}
                invitations_url = f"/api/v1/t/{TENANT_UFA}/team/invitations"

                assert (await client.post(invitations_url, json={})).status_code == 401
                created = await client.post(
                    invitations_url,
                    headers=owner_headers,
                    json={"display_name": "Новый оператор", "role": "operator"},
                )
                assert created.status_code == 201
                body = created.json()
                assert body["invitation_token"].startswith("pli_")
                assert body["member"]["state"] == "invited"
                assert "phone" not in created.text
                assert "password" not in created.text
                created_user_ids.append(UUID(body["member"]["user_id"]))
                invitation_ids.append(UUID(body["invitation_id"]))

                listed = await client.get(invitations_url, headers=owner_headers)
                assert listed.status_code == 200
                assert listed.json()[0]["state"] == "pending"
                assert "invitation_token" not in listed.text

                preview = await client.post(
                    "/api/v1/auth/staff/invitations/preview",
                    json={"invitation_token": body["invitation_token"]},
                )
                assert preview.status_code == 200
                assert preview.headers["cache-control"] == "no-store"
                assert preview.json()["organization_name"] == "Солнечный дом · демо"
                assert preview.json()["display_label"] == "Новый оператор"
                assert preview.json()["role"] == "operator"
                assert "token" not in preview.text

                simultaneous = await asyncio.gather(
                    *[
                        client.post(
                            "/api/v1/auth/staff/invitations/redeem",
                            json={
                                "invitation_token": body["invitation_token"],
                                "login_name": login_name,
                                "password": password,
                            },
                        )
                        for _ in range(2)
                    ]
                )
                assert sorted(result.status_code for result in simultaneous) == [200, 409]
                redeemed = next(result for result in simultaneous if result.status_code == 200)
                used_preview = await client.post(
                    "/api/v1/auth/staff/invitations/preview",
                    json={"invitation_token": body["invitation_token"]},
                )
                assert used_preview.status_code == 409
                assert "Новый оператор" not in used_preview.text
                assert redeemed.status_code == 200
                assert redeemed.json()["demo_only"] is False
                staff_headers = {"Authorization": f"Bearer {redeemed.json()['access_token']}"}
                memberships = await client.get("/api/v1/me/tenants", headers=staff_headers)
                assert memberships.status_code == 200
                assert memberships.json() == [
                    {
                        "tenant_id": str(TENANT_UFA),
                        "slug": "demo-ufa",
                        "display_name": "Солнечный дом · демо",
                        "timezone": "Asia/Yekaterinburg",
                        "role": "operator",
                    }
                ]
                assert (
                    await client.get(f"/api/v1/t/{TENANT_KAZAN}/context", headers=staff_headers)
                ).status_code == 404
                replay = await client.post(
                    "/api/v1/auth/staff/invitations/redeem",
                    json={
                        "invitation_token": body["invitation_token"],
                        "login_name": f"other{uuid4().hex[:8]}",
                        "password": password,
                    },
                )
                assert replay.status_code == 409

                wrong = await client.post(
                    "/api/v1/auth/staff/login",
                    json={"login_name": login_name, "password": "wrong-password-42"},
                )
                assert wrong.status_code == 401
                assert wrong.json()["detail"] == "Invalid staff credentials"
                login = await client.post(
                    "/api/v1/auth/staff/login",
                    json={"login_name": login_name, "password": password},
                )
                assert login.status_code == 200
                assert login.json()["access_token"].startswith("pl_")
                for attempt in range(5):
                    failed = await client.post(
                        "/api/v1/auth/staff/login",
                        json={
                            "login_name": login_name,
                            "password": f"wrong-password-{attempt:02d}",
                        },
                    )
                    assert failed.status_code == 401
                locked = await client.post(
                    "/api/v1/auth/staff/login",
                    json={"login_name": login_name, "password": password},
                )
                assert locked.status_code == 401
                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE staff_credentials SET "
                            "locked_until = now() - interval '1 second' "
                            "WHERE login_name = :login_name"
                        ),
                        {"login_name": login_name},
                    )
                unlocked = await client.post(
                    "/api/v1/auth/staff/login",
                    json={"login_name": login_name, "password": password},
                )
                assert unlocked.status_code == 200
                disabled = await client.patch(
                    f"/api/v1/t/{TENANT_UFA}/team/{body['member']['user_id']}",
                    headers=owner_headers,
                    json={"state": "disabled"},
                )
                assert disabled.status_code == 200
                assert disabled.json()["state"] == "disabled"
                disabled_login = await client.post(
                    "/api/v1/auth/staff/login",
                    json={"login_name": login_name, "password": password},
                )
                assert disabled_login.status_code == 401
                disabled_memberships = await client.get(
                    "/api/v1/me/tenants",
                    headers={"Authorization": f"Bearer {unlocked.json()['access_token']}"},
                )
                assert disabled_memberships.status_code == 200
                assert disabled_memberships.json() == []

                revoked = await client.post(
                    invitations_url,
                    headers=owner_headers,
                    json={"display_name": "Отозванный курьер", "role": "courier"},
                )
                revoked_body = revoked.json()
                created_user_ids.append(UUID(revoked_body["member"]["user_id"]))
                invitation_ids.append(UUID(revoked_body["invitation_id"]))
                revoked_response = await client.delete(
                    f"{invitations_url}/{revoked_body['invitation_id']}",
                    headers=owner_headers,
                )
                assert revoked_response.status_code == 200
                assert revoked_response.json()["revoked"] is True
                unavailable_preview = await client.post(
                    "/api/v1/auth/staff/invitations/preview",
                    json={"invitation_token": revoked_body["invitation_token"]},
                )
                assert unavailable_preview.status_code == 409
                assert revoked_body["invitation_token"] not in unavailable_preview.text
                rejected_revoked = await client.post(
                    "/api/v1/auth/staff/invitations/redeem",
                    json={
                        "invitation_token": revoked_body["invitation_token"],
                        "login_name": f"revoked{uuid4().hex[:8]}",
                        "password": password,
                    },
                )
                assert rejected_revoked.status_code == 409

                expired = await client.post(
                    invitations_url,
                    headers=owner_headers,
                    json={"display_name": "Истёкший администратор", "role": "admin"},
                )
                expired_body = expired.json()
                created_user_ids.append(UUID(expired_body["member"]["user_id"]))
                invitation_ids.append(UUID(expired_body["invitation_id"]))
                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE staff_invitations SET "
                            "created_at = now() - interval '2 days', "
                            "expires_at = now() - interval '1 second' "
                            "WHERE id = :invitation_id"
                        ),
                        {"invitation_id": UUID(expired_body["invitation_id"])},
                    )
                unavailable_preview = await client.post(
                    "/api/v1/auth/staff/invitations/preview",
                    json={"invitation_token": expired_body["invitation_token"]},
                )
                assert unavailable_preview.status_code == 409
                assert expired_body["invitation_token"] not in unavailable_preview.text
                rejected_expired = await client.post(
                    "/api/v1/auth/staff/invitations/redeem",
                    json={
                        "invitation_token": expired_body["invitation_token"],
                        "login_name": f"expired{uuid4().hex[:8]}",
                        "password": password,
                    },
                )
                assert rejected_expired.status_code == 409
                relisted = await client.get(invitations_url, headers=owner_headers)
                expired_item = next(
                    item
                    for item in relisted.json()
                    if item["invitation_id"] == expired_body["invitation_id"]
                )
                assert expired_item["state"] == "expired"

                async with owner.connect() as connection:
                    credential = (
                        await connection.execute(
                            text(
                                "SELECT password_hash FROM staff_credentials "
                                "WHERE login_name = :login_name"
                            ),
                            {"login_name": login_name},
                        )
                    ).one()
                    assert credential.password_hash.startswith("$scrypt$")
                    database_token = await connection.scalar(
                        text(
                            "SELECT encode(token_hash, 'hex') FROM staff_invitations "
                            "WHERE id = :invitation_id"
                        ),
                        {"invitation_id": invitation_ids[0]},
                    )
                    assert database_token != body["invitation_token"]
                    audit_changes: Sequence[object] = (
                        (
                            await connection.execute(
                                text(
                                    "SELECT changes FROM audit_events WHERE entity_id IN :ids "
                                    "ORDER BY occurred_at"
                                ).bindparams(bindparam("ids", expanding=True)),
                                {"ids": invitation_ids + created_user_ids},
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert audit_changes
                    serialized_audit = str(audit_changes).lower()
                    assert "password" not in serialized_audit
                    assert "token" not in serialized_audit
                    assert login_name not in serialized_audit
    finally:
        async with owner.begin() as connection:
            if invitation_ids or created_user_ids:
                await connection.execute(
                    text("DELETE FROM audit_events WHERE entity_id IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": invitation_ids + created_user_ids},
                )
            if created_user_ids:
                await connection.execute(
                    text("DELETE FROM users WHERE id IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": created_user_ids},
                )
        await owner.dispose()
