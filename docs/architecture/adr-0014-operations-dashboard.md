# ADR 0014: Tenant operations dashboard read model

## Status

Accepted, 2026-10-14.

## Context

The staff queue supports individual order actions, but an owner or operator needs a
single daily view before opening a specific order. The view must remain useful for
tenants with several branches and timezones, and must not become another route for
exposing customer contacts or physical asset identifiers.

## Decision

- `GET /api/v1/t/{tenant_id}/dashboard` is a computed, read-only projection. It
  runs inside the existing authorized tenant transaction and accepts only
  owner/admin/operator roles. PostgreSQL RLS remains the final isolation boundary.
- Due dates are evaluated with each order's branch IANA timezone. The response also
  carries the tenant timezone and local date for the UI heading.
- The response contains counts and capped work lists for confirmation, handover,
  returns today and tomorrow, overdue rentals, inspections and pending customer
  requests. Inventory is grouped by operational asset state.
- A dashboard order contains only its ID, immutable model/branch labels, dates,
  fulfillment/source, derived overdue days and request kinds. Customer contacts,
  delivery addresses, asset codes and serial numbers are excluded.
- The dashboard is an operational index rather than an independent workflow.
  Clicking a metric opens the existing order queue with a server-side filter. Every
  completed order action invalidates and reloads the dashboard.
- Work lists return at most eight items per group while counts remain complete. The
  order queue is the paginated source for the full set.

## Consequences

Staff can start a shift from one responsive view and move directly to the relevant
queue without duplicating order commands. Multi-branch deadlines remain correct at
date boundaries, and the summary has a smaller privacy surface than order details.

The read model currently queries the operational tables synchronously, which is
appropriate for the pilot fleet. Before much larger tenants are onboarded, query
latency and indexes must be measured with production-like volumes. Customer and
staff management, courier dispatch, financial reporting and configurable dashboard
widgets remain later stages.
