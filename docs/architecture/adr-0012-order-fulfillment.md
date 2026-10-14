# ADR 0012: Order fulfillment lifecycle and controlled contact access

## Status

Accepted, 2026-10-14.

## Context

A confirmed booking must keep its assigned physical asset unavailable while staff
hands it over, accepts the return and inspects it. Encrypted phone-order contacts
also need a narrow operational access path. Returning contact fields in the order
queue or making a returned asset immediately available would violate privacy and
inventory guarantees.

## Decision

- The order lifecycle extends to `confirmed → in_progress → returned → completed`.
  Owner, admin and operator roles issue every command with an idempotency key.
  Invalid transitions fail closed; a replay of the same command returns the current
  result without creating another event.
- Handover atomically changes the assigned asset from `available` to `rented`.
  Return changes it from `rented` to `inspection`. The confirmed allocation remains
  active through inspection, so the date range cannot be allocated again early.
- Inspection has two explicit outcomes. `passed` releases the allocation and makes
  the asset `available`; `maintenance_required` releases the allocation and moves
  the asset to `maintenance`. In both cases the order becomes `completed` and keeps
  lifecycle timestamps, staff actors and the inspection outcome.
- `POST /api/v1/t/{tenant_id}/orders/{order_id}/contact-access` is the only staff
  contact read path. It requires the same authorized tenant transaction, an
  operational order state and an idempotency key. It decrypts the order-specific
  AES-GCM snapshot, sends `Cache-Control: no-store` and writes a `contact_accessed`
  event without copying contact values into audit data. Contact data remains absent
  from list and detail DTOs and disappears from the UI when the order card closes.
- All order, allocation, asset and event writes share one PostgreSQL transaction.
  Existing FORCE RLS and composite tenant keys remain the database boundary.
  Lifecycle constraints prevent partially populated timestamps or outcomes. Catalog
  edits cannot bypass lifecycle control while an asset has an active confirmed order.

## Consequences

Staff can execute a phone booking from confirmation through inspected return while
the inventory state stays conservative. The same state appears in the customer's
privacy-minimized history without revealing staff or contact details.

This milestone does not add courier assignment, delivery routes, overdue handling,
extensions, damage charges, deposits, real payments, real SMS or a production staff
identity provider. Manual maintenance completion still uses the audited catalog
asset command. Encryption key backup and rotation, data retention and access review
remain required before real customer data is accepted.
