import hashlib
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
from app.staff_sessions import coarse_client_label

TENANT_UFA = UUID("d0000000-0000-0000-0000-000000000001")

CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
)
FIREFOX_ANDROID = "Mozilla/5.0 (Android 15; Mobile; rv:132.0) Gecko/132.0 Firefox/132.0"
SAFARI_MACOS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15"
)


def database_urls() -> tuple[str, str]:
    defaults = Settings()
    configured = make_url(os.getenv("DATABASE_URL", defaults.database_url.get_secret_value()))
    runtime = configured.set(
        username=RUNTIME_ROLE,
        password="local-runtime-only",  # noqa: S106 - synthetic test credential
    ).render_as_string(hide_password=False)
    owner = os.getenv("DATABASE_OWNER_URL", defaults.migration_database_url())
    return runtime, owner


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_client_label_is_coarse_and_drops_versions() -> None:
    assert coarse_client_label(CHROME_WINDOWS) == "Chrome · Windows"
    assert coarse_client_label(FIREFOX_ANDROID) == "Firefox · Android"
    assert coarse_client_label(SAFARI_MACOS) == "Safari · macOS"
    assert coarse_client_label("curl/8.10.0") == "API client"
    assert coarse_client_label(None) == "Browser"


@pytest.mark.integration
async def test_staff_can_inventory_and_revoke_only_their_own_sessions() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    app = create_app(Settings(app_env="test", database_url=runtime_url, enable_demo_previews=True))
    created_user_id: UUID | None = None
    invitation_id: UUID | None = None
    login_name = f"sessions{uuid4().hex[:10]}"
    password = "synthetic-password-42"  # noqa: S105 - test credential
    demo_tokens: list[str] = []
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                first_demo = await client.post(
                    "/api/v1/auth/demo-session", headers={"User-Agent": CHROME_WINDOWS}
                )
                second_demo = await client.post(
                    "/api/v1/auth/demo-session", headers={"User-Agent": SAFARI_MACOS}
                )
                assert first_demo.status_code == second_demo.status_code == 200
                demo_tokens = [
                    first_demo.json()["access_token"],
                    second_demo.json()["access_token"],
                ]
                first_demo_headers = bearer(demo_tokens[0])
                second_demo_headers = bearer(demo_tokens[1])

                first_demo_sessions = await client.get(
                    "/api/v1/auth/staff/sessions", headers=first_demo_headers
                )
                second_demo_sessions = await client.get(
                    "/api/v1/auth/staff/sessions", headers=second_demo_headers
                )
                assert [item["client_label"] for item in first_demo_sessions.json()] == [
                    "Chrome · Windows"
                ]
                assert [item["client_label"] for item in second_demo_sessions.json()] == [
                    "Safari · macOS"
                ]

                invitations_url = f"/api/v1/t/{TENANT_UFA}/team/invitations"
                created = await client.post(
                    invitations_url,
                    headers=first_demo_headers,
                    json={"display_name": "Оператор сессий", "role": "operator"},
                )
                assert created.status_code == 201
                invitation = created.json()
                created_user_id = UUID(invitation["member"]["user_id"])
                invitation_id = UUID(invitation["invitation_id"])

                redeemed = await client.post(
                    "/api/v1/auth/staff/invitations/redeem",
                    headers={"User-Agent": FIREFOX_ANDROID},
                    json={
                        "invitation_token": invitation["invitation_token"],
                        "login_name": login_name,
                        "password": password,
                    },
                )
                assert redeemed.status_code == 200
                first_staff_token = redeemed.json()["access_token"]

                chrome_login = await client.post(
                    "/api/v1/auth/staff/login",
                    headers={"User-Agent": CHROME_WINDOWS},
                    json={"login_name": login_name, "password": password},
                )
                safari_login = await client.post(
                    "/api/v1/auth/staff/login",
                    headers={"User-Agent": SAFARI_MACOS},
                    json={"login_name": login_name, "password": password},
                )
                assert chrome_login.status_code == safari_login.status_code == 200
                chrome_token = chrome_login.json()["access_token"]
                current_token = safari_login.json()["access_token"]
                current_headers = bearer(current_token)

                listed = await client.get("/api/v1/auth/staff/sessions", headers=current_headers)
                assert listed.status_code == 200
                sessions = listed.json()
                assert len(sessions) == 3
                assert sessions[0]["is_current"] is True
                assert sessions[0]["client_label"] == "Safari · macOS"
                assert {item["client_label"] for item in sessions} == {
                    "Firefox · Android",
                    "Chrome · Windows",
                    "Safari · macOS",
                }
                assert "token" not in listed.text.lower()
                assert "130.0.0.0" not in listed.text

                chrome_session_id = next(
                    item["session_id"]
                    for item in sessions
                    if item["client_label"] == "Chrome · Windows"
                )
                cross_identity = await client.delete(
                    f"/api/v1/auth/staff/sessions/{chrome_session_id}",
                    headers=first_demo_headers,
                )
                assert cross_identity.status_code == 404

                revoked = await client.delete(
                    f"/api/v1/auth/staff/sessions/{chrome_session_id}",
                    headers=current_headers,
                )
                assert revoked.status_code == 200
                assert revoked.json() == {"revoked": True, "revoked_current": False}
                assert (
                    await client.get("/api/v1/me/tenants", headers=bearer(chrome_token))
                ).status_code == 401
                remaining = await client.get("/api/v1/auth/staff/sessions", headers=current_headers)
                assert len(remaining.json()) == 2

                revoked_all = await client.delete(
                    "/api/v1/auth/staff/sessions", headers=current_headers
                )
                assert revoked_all.status_code == 200
                assert revoked_all.json() == {"revoked_count": 2}
                assert (
                    await client.get(
                        "/api/v1/auth/staff/sessions", headers=bearer(first_staff_token)
                    )
                ).status_code == 401
                assert (
                    await client.get("/api/v1/me/tenants", headers=current_headers)
                ).status_code == 401

                final_login = await client.post(
                    "/api/v1/auth/staff/login",
                    headers={"User-Agent": CHROME_WINDOWS},
                    json={"login_name": login_name, "password": password},
                )
                assert final_login.status_code == 200
                final_headers = bearer(final_login.json()["access_token"])
                final_inventory = await client.get(
                    "/api/v1/auth/staff/sessions", headers=final_headers
                )
                final_session_id = final_inventory.json()[0]["session_id"]
                revoked_current = await client.delete(
                    f"/api/v1/auth/staff/sessions/{final_session_id}",
                    headers=final_headers,
                )
                assert revoked_current.status_code == 200
                assert revoked_current.json() == {
                    "revoked": True,
                    "revoked_current": True,
                }
                assert (
                    await client.delete(
                        f"/api/v1/auth/staff/sessions/{final_session_id}",
                        headers=final_headers,
                    )
                ).status_code == 401

                async with owner.connect() as connection:
                    audit_changes: Sequence[object] = (
                        (
                            await connection.execute(
                                text(
                                    "SELECT changes FROM audit_events "
                                    "WHERE actor_user_id = :user_id "
                                    "AND action LIKE 'staff.session%' ORDER BY occurred_at"
                                ),
                                {"user_id": created_user_id},
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert len(audit_changes) == 3
                    serialized_audit = str(audit_changes).lower()
                    assert "token" not in serialized_audit
                    assert "client_label" not in serialized_audit
    finally:
        async with owner.begin() as connection:
            if invitation_id is not None:
                await connection.execute(
                    text("DELETE FROM audit_events WHERE entity_id = :invitation_id"),
                    {"invitation_id": invitation_id},
                )
            if created_user_id is not None:
                await connection.execute(
                    text("DELETE FROM audit_events WHERE actor_user_id = :user_id"),
                    {"user_id": created_user_id},
                )
                await connection.execute(
                    text("DELETE FROM users WHERE id = :user_id"),
                    {"user_id": created_user_id},
                )
            if demo_tokens:
                await connection.execute(
                    text("DELETE FROM auth_sessions WHERE token_hash IN :hashes").bindparams(
                        bindparam("hashes", expanding=True)
                    ),
                    {"hashes": [hashlib.sha256(token.encode()).digest() for token in demo_tokens]},
                )
        await owner.dispose()
