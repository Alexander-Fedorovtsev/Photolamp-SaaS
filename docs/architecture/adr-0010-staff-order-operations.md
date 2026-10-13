# ADR 0010: Staff order operations without an SMS dependency

## Status

Accepted, 2026-10-13.

## Context

The service needs an operational order queue and a safe path from a short-lived
inventory hold to a durable booking. A production SMS provider and production
staff sign-in are intentionally deferred. Catalog names can change after an order
is created, so reconstructing historical orders from current catalog rows would
make the record unstable.

## Decision

- Every new order stores a versioned `order_snapshot` containing the customer-safe
  model name, branch name, dates, fulfillment method and server quote as they were
  at order creation. Existing orders are backfilled during migration.
- Owner, admin and operator roles can list and read orders only inside their
  authorized tenant transaction. The read model contains no phone number, address,
  asset code, serial number or checkout capability.
- Staff confirmation is an explicit atomic transition from `held` to `confirmed`.
  It requires a linked, previously verified tenant customer and an unexpired hold.
  The allocation becomes `confirmed` in the same transaction and keeps the
  PostgreSQL overlap exclusion active.
- Staff cancellation is allowed from `held` or `confirmed` and cancels the linked
  allocation in the same transaction.
- Each command requires an idempotency key. Successful transitions append an
  `order_events` row with the staff actor and before/after states. Reusing a key
  for another command or order is rejected.
- Customer-facing confirmation remains unavailable. Staging keeps public booking
  disabled and payment state at `not_started`.
- The existing `SmsProvider` port remains the extension point for a future real
  provider. Development/tests may use the destination-free memory adapter;
  staging/production use the fail-closed disabled adapter. Neither adapter is
  represented as a live integration.

## Consequences

The backend now has a reviewable order lifecycle foundation and stable history,
without claiming that real staff authentication, SMS delivery, payments or the
public checkout are production-ready. Phone order creation can reuse the same
snapshot, allocation and event rules in a later stage. A real SMS adapter can be
added separately without coupling provider code to order transitions.
