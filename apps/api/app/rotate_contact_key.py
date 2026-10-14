"""Re-encrypt private contact rows with the configured active key."""

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.config import Settings
from app.contact_crypto import ContactCipher, EncryptedContact

ROTATION_LOCK_ID = 7_044_601_915_527_611_003
logger = logging.getLogger(__name__)


class RotationAlreadyRunning(RuntimeError):
    """A separate process owns the database advisory lock."""


@dataclass(frozen=True, slots=True)
class ContactTable:
    name: str
    id_column: str
    record_type: str
    count_column: str
    touches_updated_at: bool


TABLES = (
    ContactTable(
        "customer_private_profiles",
        "customer_id",
        "customer",
        "customer_profiles_rotated",
        True,
    ),
    ContactTable(
        "order_private_details",
        "order_id",
        "order",
        "order_details_rotated",
        False,
    ),
)


async def _version_counts(
    connection: AsyncConnection, tenant_id: UUID | None = None
) -> dict[str, dict[int, int]]:
    result: dict[str, dict[int, int]] = {}
    for table in TABLES:
        rows = (
            await connection.execute(
                text(
                    f"SELECT key_version, count(*) AS row_count FROM {table.name} "  # noqa: S608
                    "WHERE (CAST(:tenant_id AS uuid) IS NULL "
                    "OR tenant_id = CAST(:tenant_id AS uuid)) "
                    "GROUP BY key_version ORDER BY key_version"
                ),
                {"tenant_id": tenant_id},
            )
        ).all()
        result[table.name] = {int(row.key_version): int(row.row_count) for row in rows}
    return result


def _validate_key_coverage(counts: dict[str, dict[int, int]], cipher: ContactCipher) -> None:
    referenced = {version for versions in counts.values() for version in versions}
    missing = referenced - cipher.available_key_versions
    if missing:
        rendered = ",".join(str(version) for version in sorted(missing))
        raise ValueError(f"Contact keyring is missing database key versions: {rendered}")


async def _rotate_table_batch(
    connection: AsyncConnection,
    table: ContactTable,
    cipher: ContactCipher,
    batch_size: int,
    tenant_id: UUID | None,
) -> int:
    rows = (
        await connection.execute(
            text(
                f"SELECT tenant_id, {table.id_column} AS record_id, ciphertext, nonce, "  # noqa: S608
                f"key_version FROM {table.name} WHERE key_version <> :target "
                "AND (CAST(:tenant_id AS uuid) IS NULL "
                "OR tenant_id = CAST(:tenant_id AS uuid)) "
                f"ORDER BY tenant_id, {table.id_column} LIMIT :batch_size "
                "FOR UPDATE SKIP LOCKED"
            ),
            {
                "target": cipher.active_key_version,
                "batch_size": batch_size,
                "tenant_id": tenant_id,
            },
        )
    ).all()
    for row in rows:
        row_tenant_id = UUID(str(row.tenant_id))
        record_id = UUID(str(row.record_id))
        old = EncryptedContact(
            nonce=bytes(row.nonce),
            ciphertext=bytes(row.ciphertext),
            key_version=int(row.key_version),
        )
        payload = cipher.decrypt(row_tenant_id, table.record_type, record_id, old)
        rotated = cipher.encrypt(row_tenant_id, table.record_type, record_id, payload)
        updated_at = ", updated_at = now()" if table.touches_updated_at else ""
        result = await connection.execute(
            text(
                f"UPDATE {table.name} SET ciphertext = :ciphertext, nonce = :nonce, "  # noqa: S608
                f"key_version = :target{updated_at} WHERE tenant_id = :tenant_id "
                f"AND {table.id_column} = :record_id AND key_version = :old_version "
                "AND ciphertext = :old_ciphertext"
            ),
            {
                "ciphertext": rotated.ciphertext,
                "nonce": rotated.nonce,
                "target": rotated.key_version,
                "tenant_id": row_tenant_id,
                "record_id": record_id,
                "old_version": old.key_version,
                "old_ciphertext": old.ciphertext,
            },
        )
        if result.rowcount != 1:
            raise RuntimeError("Contact row changed during a locked rotation batch")
    return len(rows)


async def rotate_contacts(
    owner_url: str,
    cipher: ContactCipher,
    batch_size: int = 100,
    *,
    dry_run: bool = False,
    tenant_id: UUID | None = None,
) -> dict[str, Any]:
    """Rotate all known private-contact tables without exposing plaintext."""

    if not 1 <= batch_size <= 1000:
        raise ValueError("Batch size must be between 1 and 1000")
    engine = create_async_engine(owner_url)
    run_id: UUID | None = None
    acquired = False
    totals = {table.count_column: 0 for table in TABLES}
    try:
        async with engine.connect() as connection:
            acquired = bool(
                (
                    await connection.execute(
                        text("SELECT pg_try_advisory_lock(:lock_id)"),
                        {"lock_id": ROTATION_LOCK_ID},
                    )
                ).scalar_one()
            )
            await connection.commit()
            if not acquired:
                raise RotationAlreadyRunning("Another contact-key rotation is running")

            counts_before = await _version_counts(connection, tenant_id)
            _validate_key_coverage(counts_before, cipher)
            await connection.commit()
            if dry_run:
                return {
                    "target_key_version": cipher.active_key_version,
                    "versions": counts_before,
                    "dry_run": True,
                }

            run_id = uuid4()
            async with connection.begin():
                await connection.execute(
                    text(
                        "INSERT INTO contact_key_rotation_runs "
                        "(id, scope_tenant_id, target_key_version, state) "
                        "VALUES (:id, :tenant_id, :target, 'running')"
                    ),
                    {
                        "id": run_id,
                        "tenant_id": tenant_id,
                        "target": cipher.active_key_version,
                    },
                )

            for table in TABLES:
                while True:
                    async with connection.begin():
                        rotated = await _rotate_table_batch(
                            connection, table, cipher, batch_size, tenant_id
                        )
                        totals[table.count_column] += rotated
                        await connection.execute(
                            text(
                                f"UPDATE contact_key_rotation_runs SET "  # noqa: S608
                                f"{table.count_column} = :count WHERE id = :id"
                            ),
                            {"count": totals[table.count_column], "id": run_id},
                        )
                    if rotated < batch_size:
                        break

            async with connection.begin():
                counts_after = await _version_counts(connection, tenant_id)
                remaining = sum(
                    count
                    for versions in counts_after.values()
                    for version, count in versions.items()
                    if version != cipher.active_key_version
                )
                if remaining:
                    raise RuntimeError("Contact rows remain on non-active key versions")
                await connection.execute(
                    text(
                        "UPDATE contact_key_rotation_runs SET state = 'succeeded', "
                        "finished_at = now() WHERE id = :id"
                    ),
                    {"id": run_id},
                )
            return {
                "run_id": str(run_id),
                "target_key_version": cipher.active_key_version,
                **totals,
                "remaining_non_active": 0,
            }
    except Exception as error:
        if run_id is not None:
            try:
                async with engine.begin() as failure_connection:
                    await failure_connection.execute(
                        text(
                            "UPDATE contact_key_rotation_runs SET state = 'failed', "
                            "failure_code = :code, finished_at = now() "
                            "WHERE id = :id AND state = 'running'"
                        ),
                        {"code": type(error).__name__[:80], "id": run_id},
                    )
            except Exception:
                logger.exception("Could not persist failed contact-key rotation state")
        raise
    finally:
        if acquired:
            # Closing the owning connection releases this session-level lock.
            acquired = False
        await engine.dispose()


async def assert_key_version_unused(
    owner_url: str, key_version: int, tenant_id: UUID | None = None
) -> dict[str, Any]:
    """Refuse retirement while any private row still references the key version."""

    if not 1 <= key_version <= 32767:
        raise ValueError("Contact key version must be between 1 and 32767")
    engine = create_async_engine(owner_url)
    try:
        async with engine.connect() as connection:
            acquired = bool(
                (
                    await connection.execute(
                        text("SELECT pg_try_advisory_lock(:lock_id)"),
                        {"lock_id": ROTATION_LOCK_ID},
                    )
                ).scalar_one()
            )
            if not acquired:
                raise RotationAlreadyRunning("Another contact-key rotation is running")
            counts = await _version_counts(connection, tenant_id)
        references = {table: versions.get(key_version, 0) for table, versions in counts.items()}
        if any(references.values()):
            raise ValueError(
                f"Contact key version {key_version} is still referenced by database rows"
            )
        return {"key_version": key_version, "references": references, "unused": True}
    finally:
        await engine.dispose()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-version", type=int)
    parser.add_argument("--assert-unused-version", type=int)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


async def _main() -> None:
    arguments = _arguments()
    settings = Settings()
    if settings.database_owner_url is None:
        raise RuntimeError("DATABASE_OWNER_URL is required for contact-key rotation")
    if arguments.assert_unused_version is not None:
        if arguments.target_version is not None or arguments.dry_run:
            raise ValueError("Unused-key check cannot be combined with rotation options")
        if arguments.assert_unused_version == settings.contact_active_key_version:
            raise ValueError("The active contact key cannot be retired")
        if arguments.assert_unused_version not in settings.contact_keyring():
            raise ValueError("The retiring contact key is absent from the keyring")
        result = await assert_key_version_unused(
            settings.database_owner_url.get_secret_value(),
            arguments.assert_unused_version,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return
    if (
        arguments.target_version is not None
        and arguments.target_version != settings.contact_active_key_version
    ):
        raise ValueError("Target version must match CONTACT_ACTIVE_KEY_VERSION")
    cipher = ContactCipher(settings.contact_keyring(), settings.contact_active_key_version)
    result = await rotate_contacts(
        settings.database_owner_url.get_secret_value(),
        cipher,
        arguments.batch_size,
        dry_run=arguments.dry_run,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    asyncio.run(_main())
