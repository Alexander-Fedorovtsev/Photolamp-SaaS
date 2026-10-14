# ADR 0013: Extension and return requests with fail-closed notifications

## Status

Accepted, 2026-10-14.

## Context

An active rental needs customer-initiated extension and return requests without
turning either request into a lifecycle state. An extension must not silently take
an asset already promised to another customer. The product also needs a durable
notification boundary while staging intentionally has no real SMS provider or
background worker.

## Decision

- `order_extension_requests` and `order_return_requests` are tenant-owned RLS
  entities. A verified customer session can create a request only for its own
  `in_progress` order. Every command has an idempotency key and command fingerprint;
  at most one pending request of each kind exists per order.
- An extension stores its original and requested end dates, incremental days and
  price calculated from the immutable order quote. Approval locks the request,
  order and allocation, checks the assigned asset against future active allocations
  and asset blocks, then updates the effective order/allocation end date in one
  transaction. The original order snapshot remains unchanged.
- Staff can approve or reject an extension and acknowledge or reject a return
  request. Each decision records the staff actor and a privacy-safe order event.
  Customer-created events use a tenant-scoped customer actor foreign key.
- Overdue is derived from an active order's effective end date in the branch IANA
  timezone. It is returned as `is_overdue` and `overdue_days`, and the staff queue
  can filter overdue orders. It is not an order state.
- Handover plans one `return_due_24h` and one `return_overdue` notification job.
  Extension approval cancels the old pair and plans a new pair. Decisions also
  create deduplicated jobs. Jobs contain tenant, order and customer IDs but no
  plaintext destination. With the provider disabled every new job is explicitly
  `blocked/provider_disabled`; no delivery attempt or success is recorded.
- The current date-only rental model schedules the due reminder for local noon on
  the previous day and the overdue reminder for 09:00 on the following day. A future
  time-window model can make these timestamps exact without changing request rules.

## Consequences

Customers and staff now have an auditable request workflow, and extension approval
remains protected by PostgreSQL overlap constraints as well as an explicit conflict
check. Staff can see overdue work without multiplying lifecycle states. The outbox
is ready for a tenant-aware worker and provider adapter while staging continues to
fail closed.

There is still no active notification worker, real SMS delivery, customer cabinet
UI, payment collection for extension amounts, courier assignment or automatic fee
policy. Enabling delivery later requires a provider choice, destination resolution
from encrypted customer data, retry/attempt records, rate limits and operational
monitoring.
