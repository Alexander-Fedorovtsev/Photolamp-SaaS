# ADR 0020: Versioned contact-key rotation

## Status

Accepted, 2026-10-14.

## Context

Customer phones, recipient names and delivery addresses are encrypted with
AES-256-GCM. Keeping one unversioned environment key would make planned rotation
either destructive or dependent on a long maintenance window. A partial run must
not make existing rows unreadable, cross tenant boundaries or expose plaintext in
logs. The API runtime must not receive database-owner credentials.

## Decision

- `CONTACT_ENCRYPTION_KEYS` is a bounded JSON keyring keyed by positive integer
  versions. `CONTACT_ACTIVE_KEY_VERSION` selects the key for new writes. The
  existing `key_version` columns select the key used for reads.
- Every configured key is a distinct 32-byte URL-safe base64 value. Production
  rejects the documented local key. An unavailable row version fails closed.
- The API receives the keyring with its least-privilege runtime database login.
  A profile-only `contact-maintenance` container receives the same keyring and
  owner database URL. It uses the exact CI-built API image, the internal network,
  a read-only filesystem, dropped capabilities and no published port.
- Rotation first validates that every referenced database version exists in the
  keyring. It then acquires a PostgreSQL advisory lock and re-encrypts customer
  profiles and order details in bounded transactions with row locks. Each update
  compares the old key version and ciphertext. A stopped run is resumed safely
  because completed rows already use the target version.
- `contact_key_rotation_runs` records target/scope, state and row counts. It never
  stores ciphertext, plaintext, phone fragments or key material.
- The host helper activates the new key and restarts the API before rotating old
  rows, so concurrent application writes use only the new version. An old key can
  be removed by the retirement helper only after a database-wide reference check.
- First deployment converts the legacy environment setting without changing its
  key bytes. Existing staging installs that relied on the historical implicit
  synthetic key preserve it as version 1.

## Consequences

Normal reads and writes remain available during rotation. A failure leaves both
keys available and already converted batches readable; rerunning the maintenance
command completes the work. Losing any still-referenced key remains unrecoverable,
so the full keyring must be kept in protected off-host custody before real data is
accepted. This mechanism does not replace secret-manager access controls, contact
retention/deletion policy, external backups or the legal production gate.
