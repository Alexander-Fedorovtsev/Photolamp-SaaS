import base64
import os
import secrets
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.contact_crypto import ContactCipher, EncryptedContact
from app.rotate_contact_key import assert_key_version_unused, rotate_contacts


def owner_database_url() -> str:
    defaults = Settings()
    configured = make_url(os.getenv("DATABASE_OWNER_URL", defaults.migration_database_url()))
    return configured.render_as_string(hide_password=False)


@pytest.mark.integration
async def test_rotation_reencrypts_rows_and_records_a_secret_free_run() -> None:
    owner_url = owner_database_url()
    engine = create_async_engine(owner_url)
    tenant_id = uuid4()
    customer_id = uuid4()
    old_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    new_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    old_cipher = ContactCipher({1: old_key}, 1)
    rotating_cipher = ContactCipher({1: old_key, 2: new_key}, 2)
    payload = {"phone_e164": "+70000000000", "recipient_name": "Тестовый клиент"}
    encrypted = old_cipher.encrypt(tenant_id, "customer", customer_id, payload)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO tenants (id, slug, display_name, timezone) "
                    "VALUES (:id, :slug, 'Rotation test', 'UTC')"
                ),
                {"id": tenant_id, "slug": f"rotation-{tenant_id.hex}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO customers "
                    "(id, tenant_id, phone_lookup_hash, phone_last4) "
                    "VALUES (:id, :tenant, :lookup, '0000')"
                ),
                {"id": customer_id, "tenant": tenant_id, "lookup": secrets.token_bytes(32)},
            )
            await connection.execute(
                text(
                    "INSERT INTO customer_private_profiles "
                    "(tenant_id, customer_id, ciphertext, nonce, key_version) "
                    "VALUES (:tenant, :id, :ciphertext, :nonce, 1)"
                ),
                {
                    "tenant": tenant_id,
                    "id": customer_id,
                    "ciphertext": encrypted.ciphertext,
                    "nonce": encrypted.nonce,
                },
            )

        result = await rotate_contacts(
            owner_url, rotating_cipher, batch_size=1, tenant_id=tenant_id
        )

        assert result["target_key_version"] == 2
        assert result["customer_profiles_rotated"] >= 1
        assert result["remaining_non_active"] == 0
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT ciphertext, nonce, key_version FROM customer_private_profiles "
                        "WHERE tenant_id = :tenant AND customer_id = :id"
                    ),
                    {"tenant": tenant_id, "id": customer_id},
                )
            ).one()
            run = (
                await connection.execute(
                    text(
                        "SELECT state, failure_code FROM contact_key_rotation_runs WHERE id = :id"
                    ),
                    {"id": UUID(result["run_id"])},
                )
            ).one()
        assert row.key_version == 2
        assert run.state == "succeeded"
        assert run.failure_code is None
        assert (
            rotating_cipher.decrypt(
                tenant_id,
                "customer",
                customer_id,
                EncryptedContact(bytes(row.nonce), bytes(row.ciphertext), row.key_version),
            )
            == payload
        )
        unused = await assert_key_version_unused(owner_url, 1, tenant_id)
        assert unused["unused"] is True
        with pytest.raises(ValueError, match="still referenced"):
            await assert_key_version_unused(owner_url, 2, tenant_id)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM contact_key_rotation_runs WHERE scope_tenant_id = :tenant"),
                {"tenant": tenant_id},
            )
            await connection.execute(
                text("DELETE FROM tenants WHERE id = :tenant"), {"tenant": tenant_id}
            )
        await engine.dispose()


@pytest.mark.integration
async def test_rotation_fails_before_writes_when_a_referenced_key_is_missing() -> None:
    owner_url = owner_database_url()
    engine = create_async_engine(owner_url)
    tenant_id = uuid4()
    customer_id = uuid4()
    old_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    active_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    cipher = ContactCipher({2: active_key}, 2)
    encrypted = ContactCipher({1: old_key}, 1).encrypt(
        tenant_id, "customer", customer_id, {"phone_e164": "+70000000000"}
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO tenants (id, slug, display_name, timezone) "
                    "VALUES (:id, :slug, 'Rotation test', 'UTC')"
                ),
                {"id": tenant_id, "slug": f"rotation-{tenant_id.hex}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO customers "
                    "(id, tenant_id, phone_lookup_hash, phone_last4) "
                    "VALUES (:id, :tenant, :lookup, '0000')"
                ),
                {"id": customer_id, "tenant": tenant_id, "lookup": secrets.token_bytes(32)},
            )
            await connection.execute(
                text(
                    "INSERT INTO customer_private_profiles "
                    "(tenant_id, customer_id, ciphertext, nonce, key_version) "
                    "VALUES (:tenant, :id, :ciphertext, :nonce, 1)"
                ),
                {
                    "tenant": tenant_id,
                    "id": customer_id,
                    "ciphertext": encrypted.ciphertext,
                    "nonce": encrypted.nonce,
                },
            )
        with pytest.raises(ValueError, match="missing database key versions"):
            await rotate_contacts(owner_url, cipher, dry_run=True, tenant_id=tenant_id)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM tenants WHERE id = :tenant"), {"tenant": tenant_id}
            )
        await engine.dispose()
