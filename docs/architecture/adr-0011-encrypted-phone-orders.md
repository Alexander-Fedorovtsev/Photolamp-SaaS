# ADR 0011: Encrypted contacts and staff-created phone orders

## Status

Accepted, 2026-10-14.

## Context

An operator must be able to finish a booking during a phone call while public
checkout and a real SMS provider remain disabled. Phone numbers and delivery
addresses are private customer data. They must not become cross-tenant lookup
keys, plaintext database fields, API logs or fields in the broad staff queue.
The phone flow must obey the same catalog, pricing, date and physical inventory
constraints as the website flow.

## Decision

- `POST /api/v1/t/{tenant_id}/orders/phone` is available to owner, admin and
  operator roles through the existing authenticated tenant transaction. It
  requires an idempotency key and creates a confirmed order attributed to the
  staff user. No OTP or SMS success is claimed.
- The server loads the published model, active branch and active tariff, checks
  the inclusive date range and fulfillment mode, locks one available asset with
  `FOR UPDATE SKIP LOCKED`, and writes the confirmed allocation, immutable order
  snapshot, private details and audit event in one database transaction. The
  existing PostgreSQL exclusion constraint remains the final overlap guard.
- Phone lookup uses HMAC-SHA256 over `tenant_id || phone_e164` with `OTP_PEPPER`.
  The digest is useful only inside one tenant and the phone cannot be recovered
  from it. A later successful OTP for the same tenant and phone updates the same
  customer row, marks the phone verified and exposes the existing phone order in
  that customer's privacy-minimized history.
- Recoverable contact data is encrypted by the API with AES-256-GCM. A random
  96-bit nonce is generated for every write. Authenticated additional data binds
  the ciphertext to its schema version, record type, tenant and record UUID.
  Customer profile and order-specific delivery fields use separate ciphertexts.
- `customer_private_profiles` and `order_private_details` use composite tenant
  foreign keys, FORCE RLS, least-privilege grants and no public access. The staff
  list/detail contract does not decrypt or return contact fields. Key version is
  stored so a later rotation workflow can be introduced without changing rows.
- Production requires a non-demo 32-byte URL-safe base64 encryption key and a
  non-demo phone lookup pepper. The local/staging default is accepted only for
  synthetic demo operation. Demo tenants reject every contact except the fixed
  `+70000000000`, `Тестовый клиент` and conditional `Тестовый адрес` values.
- The existing `SmsProvider` remains disabled in staging. Sending a booking link
  is a future adapter and job; order creation does not call it.

## Consequences

Operators can now create an auditable booking without customer interaction, and
the customer identity can be joined later without a global phone directory.
Database theft alone does not reveal contact plaintext when the application key
is stored separately. RLS and authenticated encryption provide independent
tenant-bound controls.

ADR 0012 adds a narrow, audited screen and command that decrypts order contact
details only during fulfillment. ADR 0020 adds versioned keys and resumable
re-encryption. Before real customers are accepted, the access policy still needs
operational review together with off-host key custody, retention rules, secret
management and the legal production gate.
