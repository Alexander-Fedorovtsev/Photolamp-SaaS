# ADR 0019: Encrypted backups and restore drills

## Status

Accepted, 2026-10-14.

## Context

Staging PostgreSQL persists on the host RAID, but persistence alone does not
protect against an operator mistake, logical corruption or an unusable dump.
The project also needs a rotation path that does not make archives encrypted
with a previous key unreadable.

## Decision

- A host maintenance script creates a PostgreSQL custom-format dump and streams
  it through AES-256-GCM authenticated encryption. Plaintext dumps are never
  written to the host filesystem.
- Backup keys live only in the protected server `.env` and the short-lived
  `backup-crypto` container. That container uses the CI-tested API image, has no
  network, database credentials, capabilities or writable root filesystem. The
  long-running API, web and database containers do not receive backup keys.
- Every archive records a non-secret key ID. `BACKUP_ENCRYPTION_KEYS` is a
  versioned keyring and `BACKUP_ACTIVE_KEY_ID` selects the key for new archives.
  Rotation adds a new active key while retaining old keys until their archives
  expire.
- Archives and SHA-256 sidecars are written atomically with mode 600 under
  `/mnt/raid/photolamp-test/backups`. Retention defaults to 30 days and at most
  35 archives. Backup and deploy operations share the same host lock.
- A backup succeeds only after checksum validation, authenticated decryption and
  a real `pg_restore` into a randomly named disposable database. The drill checks
  Alembic metadata, core relations and forced RLS on private contact tables, then
  force-drops the disposable database.
- CD performs a backup and restore drill after each successful deployment. A
  separate GitHub Actions workflow repeats it daily at 01:17 UTC and can also be
  started manually. Workflow logs contain archive names and status, never keys or
  database content.

## Consequences and limits

The staging release now proves that a fresh encrypted dump can be restored with
the currently deployed schema and that archives made before a backup-key rotation
remain readable while their keys are retained.

These copies remain on the same physical host. They protect against logical
failure, but not loss of the host or RAID. Before accepting real customer data,
the encrypted archives need a separately credentialed off-host object store,
immutability/versioning and a second restore drill from that store. The keyring
must be escrowed in an off-host password manager; losing both `.env` and that
copy makes the archives unrecoverable. Point-in-time recovery/WAL archiving and
contact-field key re-encryption are separate stages. SMS remains disabled.
