# ADR 0024: Assigned courier delivery workspace

## Status

Accepted, 2026-10-15.

## Context

Couriers need enough information to complete a delivery, but the shared order
queue, inventory identifiers, staff directory and customer contacts exceed that
role's duties. A delivery also has to move the same order and physical asset
through the existing fulfillment transaction instead of creating a second,
inconsistent lifecycle.

## Decision

- A tenant-scoped `courier_tasks` table records one outbound delivery assignment
  per order. FORCE RLS, composite tenant foreign keys and membership foreign keys
  preserve the existing tenant boundary.
- Owner, admin and operator roles can assign an active courier to a confirmed
  delivery. They receive only a minimal courier selector and assignment status.
  An accepted task cannot be reassigned; cancellation closes an outstanding task.
- A courier can list only tasks assigned to the current membership. The task read
  model contains the immutable order snapshot, branch and rental dates, but no
  customer contact, asset code, serial number, other orders or staff directory.
- The task lifecycle is `assigned → accepted → picked_up → delivered`. Commands
  require idempotency keys and append actor-attributed order events. Pickup
  atomically performs the existing handover: the order becomes `in_progress`, the
  physical asset becomes `rented`, and return notifications are planned.
- Contact access is a separate command available only after acceptance and before
  delivery. It decrypts the order-specific contact snapshot, requires the current
  assignee, uses `Cache-Control: no-store`, and writes a contact access event
  without copying contact values into audit payloads. The browser discards revealed
  contact data on refresh, status transition or role change.
- The first release supports outbound delivery only. Return pickup tasks, route
  planning, geocoding, proof files, push delivery and active SMS remain separate
  stages. The UI and API do not claim those integrations are active.

## Consequences

Operators can dispatch a delivery and the courier can complete it from a narrow,
mobile workspace while inventory state and order audit stay consistent. Delivered
tasks remain as bounded operational history; future route and return workflows can
extend the task model without broadening courier access to the administrative API.
